# Changelog

All notable changes to AI Office, written for the people who run it rather than for the people
who wrote it. Dates are the day the change reached the public repository.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The version you are running is printed
by `python3 server/office_version.py` and returned by `GET /api/office` as `app.version`.

## [3.0.0] — AI Office 3.0, 2026-11-14

*The first public release of 3.0 — everything since the third members' beta. Two requests drove most of it: "I can't tell what changed — change
the screen itself, think of the Claude app" (the Mac), and "the phone is roundabout — make answering as quick
as Claude's own app" (the phone). Then the bots you run got a seat in the office.*

### Changed — a new default screen on the Mac
- **The bright three-column screen is the default.** Left: what's waiting on you, then every session, grouped
  by project and searchable. Middle: the selected session's conversation the way the Claude Code app shows it
  — your instruction, its reply, the tools it ran folded into one line you can open, and, when it's waiting,
  the question with its actual options as buttons. Right: the office (3D for the whole room, a pixel-art
  close-up of the person you picked) and what that session is doing, its todo list and the files it changed.
  The Cockpit, the 3D office and the ledger are still one click away in ⚙ settings → *Screen*.
- In the room, only the person you picked and the ones waiting on you wear a name tag, so a busy office
  stays readable.

### Added — your bots get a seat (the *door*)
- **Resident bots and scripts can check in, report, ask you a question, read your answers and withdraw a
  question** through five MCP tools of their own (`mcp_office.py --bot <id>`). A bot is one more person in the
  office: its question shows up in "waiting on you" on the Mac and the phone, with its options as buttons.
  Bots can never approve anything and can't send instructions to your sessions.
- **⚙ settings → Bots** lists your bots (last activity, open question, unread items), adds and removes them,
  mutes one (no sound or Mac notification for its questions) and copies the one-line register command to run
  in that bot's own work folder. Registering, muting and deleting work only from the Mac's own screen.
- **A bot's record**: pick a bot and the middle column shows its check-ins, reports, its question and your
  answer, with dates. The phone shows the same record.
- Bots have their own look — a box head with a lamp on top — in the pixel art, the top-down room and 3D.
- **Gentle nudges**: the Mac tells you when a session has stopped or when something has been waiting on you
  for too long.

### Added — today's card
- **One picture of your day to share.** *Today's card* at the bottom of the left column makes a 1200×675 PNG
  of the room in pixel art and today's numbers — sessions, questions answered, commits, hours of work, tool
  runs. It never contains a name: the numbers carry no names and the room is drawn without text.

### Changed — the phone
- **Answering from the phone works for every kind of question.** Multi-question prompts carry every question
  and you answer each one; when approvals are off or the phone isn't set up for them, the card says why and
  how to fix it.
- **Approvals are one tap while they're on.** No passkey and no Face ID; you switch phone approvals on and off
  from the phone or the Mac, and they stay on until you switch them off. Dangerous requests get a red line on
  the card; each tap is bound to the exact request you were shown and re-checked on the Mac.
- **Extend a phone's pairing from the Mac** before it expires; the new secret reaches the phone sealed, never
  in a link.
- **A session's conversation, as it is**: thinking marks, the full Bash command, diffs, project-relative paths,
  search terms, the last lines of each sub-agent — and Codex sessions on the same screen.
- **Three screens instead of four tabs**: Conversations (what's waiting on you and recent sessions), Office (the
  room and everyone in it) and Settings, laid out as cards. Layouts follow the shape of your phone, including
  narrow and unusually tall screens.
- **On a wide screen it looks like the Mac.** An unfolded iPhone Duo or an iPad in landscape shows the Mac's three
  columns side by side — what's waiting on you and your sessions, the conversation, and the office — with no tab
  bar; in portrait it shows the list and the conversation, and the office opens over the right column.
- **Always the Mac's light colours.** The phone no longer switches to a dark, terminal-style look when the iPhone
  is in dark mode, and a session's one-line summaries read like the conversation view instead of a terminal.

### Fixed
- The office no longer makes you wait one to three minutes after the app restarts, and the background scan no
  longer stalls for minutes while the Mac is busy with heavy work.
- Pressing an answer button in the middle of the screen could be lost if new data arrived while the button was
  held down; it now waits until you let go.
- Selecting text in the conversation was cleared about once a second; the middle column now redraws only when
  something in it actually changed.

## [3.0.0-beta.3] — members' beta, 2026-10-07

*Third pre-release for the same group. Driven by one request on the Mac screen: "let me use it like a
terminal or an IDE — split the terminal, move the windows around, drag the borders to resize."*

### Added — arrange the cockpit like an IDE (Mac, three-column layout)
- **Move the windows.** Drag a window's title bar onto another window to swap them. The room, Reception,
  the terminal and the Roster can go in any of the three columns, and two of them can share a column.
  Right-click the title bar, or focus it and press Alt+Arrow, for the same moves without a mouse. The
  room always stays whole at ×2/×3/×4; the terminal column keeps at least 40 columns.
- **Drag the borders.** The 8px gutters between columns (and between two stacked windows) are now
  handles: drag to resize the two neighbours, double-click to restore. The room snaps to whole pixel
  multiples; the roster stays between 240 and 520px.
- **Split the terminal.** `⌘\` splits the terminal pane to the right, `⌘⇧\` (or `⌘⌥\`) downward, up
  to four panes; `⌘⌥X` closes the focused pane and `⌘⇧←→↑↓` moves between panes. "+ Terminal" and the
  session tabs open in the focused pane; a session without a terminal shows its conversation in the
  focused pane. Each terminal appears in one pane at a time. The arrangement is remembered per browser;
  Settings → *Layout* → *Reset* puts everything back.
- Text never shrinks when a pane or column gets narrow — you get fewer columns instead (down to 40).
  If a split would not fit, the cockpit says so instead of hiding your terminal.

### Added — extend a phone's pairing from the Mac (same phone, no new QR code)
- A paired phone stops working 30 days after it was paired. Re-pairing meant scanning a new QR code,
  which on an iPhone opens Safari rather than the app on your home screen. Now each phone in the Mac's
  **Phone** panel has **Extend (30 days)**: confirm it on the Mac, then open the app on the phone, and
  it carries on for another 30 days without re-pairing.
- Extending replaces the phone's pairing secret rather than stretching the old one. The new secret is
  sent to the phone sealed with a key derived from its current one (the relay only ever holds
  ciphertext) and never appears in a link, so a pairing link left in a browser history stops working
  at the first extension. The Mac switches over only after the phone answers with the new secret, and
  accepts the old one for ten more minutes so nothing in flight is lost. Revoked or already expired
  phones cannot be extended; the Mac notifies you when an extension is armed and when it completes.
- The phone now warns five days before its pairing ends (it was three) and points to the Mac's
  Extend button. If you suspect a pairing secret has leaked, revoke the phone and pair it again
  instead of extending it.

### Fixed — the office view stalled for seconds to minutes while the Mac was busy
- When something heavy ran on the same Mac (exporting a video, a sync client, a long test run), the
  office view, the phone's snapshot and the cockpit could stop refreshing for ten seconds to three
  minutes. The two background services (the server and the relay agent) were installed without a
  launchd `ProcessType`, and macOS runs such jobs in a throttled class — lower CPU priority and
  throttled disk reads — so every small read in a refresh waited behind the heavy work (measured on
  the same Mac under the same load: about one second in the normal class, ten in the throttled one).
  Both now run as `Interactive`, the same class as apps. Shells opened from the cockpit's terminal
  inherited the same throttling and no longer do.
- An existing install picks this up after re-running `macapp/install.sh` and reloading both agents
  (`launchctl bootout`, then `bootstrap` — a `kickstart` alone does not reread the plist).

### Changed — the phone app on a foldable (iPhone Duo) opened wide
- On a wide, not-so-tall screen in landscape — the iPhone Duo opened flat (about 890×626) — the left
  column of the three-column view now widens enough to draw the room at ×2 instead of ×1, even while
  something is waiting for you. The terminal in the middle gets narrower to make room (about 360px on
  the Duo). The first waiting item is no longer repeated under the room at that height; the right
  column and the card in the middle already show it. Phones held sideways, tablets, and the Duo's
  other shapes (folded, or opened and held upright) look the same as before.

### Changed — approving from the phone: one tap while it is on
- Turning *Phone approvals* on still takes Face ID (and lasts 1/4/12 hours). While it is on, routine and
  review-tier requests are approved with **one tap, no Face ID** — the phone signs the approval with its
  pairing key, and the Mac still checks the registered key, the open window, the posted request's
  identity and freshness, rate limits and the risk tier before letting it through (the hook checks all of
  that independently). Dangerous requests (force push, git config, writes under `~/.claude`, anything
  the policy cannot read) always require the passkey (one Face ID) and never pass by tap; they now show
  the essentials with the full text optional instead of forcing a scroll-to-the-end and a checkbox.
- If the app is opened in a browser tab that has no pairing (for example from a notification in Safari),
  it now says "open it from the home-screen icon" instead of sending you to the Mac's QR screen.
- The security notes record the widened residual risk: a stolen pairing secret can approve routine and
  review-tier requests without Face ID while the window is open; everything is still notified and audited.

### Added — the phone's session screen shows the real work (Claude Code and Codex)
- **Claude Code sessions, closer to the terminal.** The session screen now shows a "thought" marker
  (how many times and roughly how many characters — never the text), the full Bash command and its
  output (up to 4,000 / 8,000 characters; failures show head and tail), diffs up to 100 lines, file paths
  relative to the project, search terms, fetched URLs (without query strings), and the sub-agents' last
  lines folded under their task. Everything still travels sealed end-to-end and scrubbed for secrets.
- **Codex sessions can be opened too.** The Mac reads the Codex session log read-only, confined to
  Codex's own folders, and maps it onto the same rows (shell/exec commands and their output, file
  changes, plan updates, reasoning as a marker). The vendor dot marks Claude / Codex / OpenClaw in the
  list, the sheet and the screen header.
- **One tap to allow from the phone.** Settings now has a single primary button, "Let this phone allow":
  it registers the passkey and turns phone approvals on for 12 hours in one flow (two Face ID prompts,
  two separate signatures as before). The screen tells you exactly where you are (not registered /
  registered but off / on until HH:MM / phone changed), the wrong "press Register on the Mac" hint is
  gone, double taps are ignored, re-pairing warns that the approval key must be registered again, and
  the result of an approval shows as a toast wherever you are.

### Fixed — the phone could not answer
- **Multi-question prompts.** When Claude Code asks two or more questions at once (or a multi-select
  question), the phone used to show the options greyed out ("answer this on the Mac"). The posting now
  carries every question with its options, and the phone lets you pick an answer per question (check
  boxes for multi-select) and sends them as one written answer — the same path a typed answer takes.
- **"Allow on Mac" with no way forward.** When phone approvals are off (the timed switch expired) or this
  phone has no approval passkey, the permission card now says exactly why and offers the fix in place
  ("Turn on in Settings", "Register this phone"); Settings shows the expired/unregistered state in red.
  Deny and "Write…" stay available either way.
- The phone reports its own JavaScript errors and failed sends to the relay's log (counts and error
  text only — never the instruction text, session or request ids), so the next silent failure is visible.

### Fixed — the Mac's office view froze after a restart, and stalled once a minute
- Right after the daemon started, `GET /api/office` (the room, the roster, the phone's snapshot) could
  hang for one to three minutes, and afterwards every request stalled for about three seconds once a
  minute and for half a second every few seconds. The cause was not the transcripts: the XP
  calculation read **every** hook row from the timeline database (328,800 rows) into memory every
  minute and recounted 350,000 facts on every poll, the resource gauges fetched provider APIs over
  HTTPS, and all of it ran while holding the lock that every request waits on. Both daemons had grown
  to about 1.1 GB.
- The office is now served **stale-while-revalidate**: a request gets the last result at once while a
  single background scan refreshes it (the first request after a start still waits for one scan, now
  under a second; a request after a quiet spell waits for one fresh scan of about 0.3 s). Your own
  settings changes are read back immediately. The live-update stream waits for the refreshed value
  before it tells the page to refetch, and follows up if the scan was slow.
- The XP and digest readers select only the rows they need (about 1% of the table) and cache the small
  result, not the rows; the result is identical to the old reader on the real database (checked
  JSON-for-JSON, including the day-by-day digest). Memory dropped from about 800 MB to about 250 MB,
  and the remaining peak is the resource gauges' first read, which now runs off the request path.
- The phone no longer posts an unauthenticated diagnostic when it is opened without a pairing (the
  relay rejected it anyway).

### Changed — approving from the phone: one tap for everything, always on (R118)
- **One tap, like the Claude app.** While *Phone approvals* is on, every request the phone can show is
  approved with **one tap** — routine, review-tier and dangerous ones alike (deleting files, force push,
  git config, writes under `~/.claude`). Dangerous requests still carry a red line on the card naming the
  kind of risk. There is no Face ID step and no passkey to register any more: the phone signs each
  approval with its pairing key, bound to the exact request and command it showed (request id and
  SHA-256), and the Mac and the hook each re-check the pairing, the request's identity and freshness, the
  rate limit and that the request has not become more dangerous since it was shown.
- **Always on.** Turning phone approvals on no longer asks for Face ID and no longer expires: it stays on
  until you turn it off in the phone's Settings or on the Mac. Settings → Approvals is one line and one
  button (**Turn on** / **Turn off**). The Mac is notified and logs every switch and every approval.
- Unchanged: forms the policy cannot read (oversized or unreadable input, commands assembled on the fly,
  download-and-run, unknown tools) never reach the phone and stay Mac-only; the relay can still only
  deliver words, not permissions; the signed formats and their test vectors are unchanged.
- **Wider residual risk, accepted.** Anyone holding this phone's pairing secret (30 days) can now approve
  anything Claude asks for — dangerous requests included — whenever approvals are on, and "on" no longer
  runs out. The mitigations are the Mac notification and audit entry for every approval, instant
  switch-off and revocation from the Mac, the 30-day pairing expiry, and keeping a single paired phone
  (the older pairing was revoked). Details in `docs/security-notes.md`.
- This replaces the Face ID step for dangerous requests, the passkey registration, the 1/4/12-hour timed
  switch and **Register again** described in the other phone-approval entries of this release.

### Changed — approving from the phone: one decision card (R116-S1)
- **A card instead of a screen.** Every permission request now shows up as one card — on the office
  tab, the "waiting on you" tab, inside a session and in the session sheet — with the name, how long
  it has waited, the tool, the command (four lines, twelve on the waiting tab; "+N lines" and **Full
  text** when it is longer) and three buttons: **Allow** (or **Allow with Face ID** for dangerous
  operations), **Deny** and **Deny with a note…**. The full-screen approval page, its "Close" buttons
  and the "Review & allow here" buttons are gone; there is exactly one Allow button on the page.
  On the office tab the card takes the place of the "waiting on you" box when a permission request is
  first in line (the tab still shows how many are waiting); other requests on the "waiting on you" tab
  are one line each and become the card when tapped. Plan approvals still read **Approve on Mac**.
  Inside a session the card sits under the conversation and scrolls on its own with the buttons
  pinned to its bottom; the reply box folds away while it is shown (**Deny with a note…** does the
  same job).
- **Fix it where you are.** When phone approvals are off or this phone has no approval key, the card
  itself offers **Turn on & review** (Face ID once) or **Let this phone allow** (register, then turn
  on) instead of sending you to Settings; once that succeeds, the command appears in the same card.
- **Straight to the next one.** As soon as the relay has your answer, the card moves to the next
  request. The result appears in the status line ("✓ Allow — web-app"); if the Mac turns it down, one
  of eight short failure lines says why and the request comes back. A second approval within five
  seconds no longer bounces off the Mac's rate limit — the phone waits and sends it, and sends nothing
  if you leave the app meanwhile. A card you are looking at no longer jumps when a new request arrives.
- **Quieter.** Approval-related text went from about 175 strings to 38; danger reasons are shown as
  one of nine kinds ("deleting", "sending out", …) next to the command. Settings → Approvals is one
  line and one button (plus 1 h / 4 h while off). The paste box for a Mac registration link is gone
  (registration has been phone-only since R103-1).
- **Keeps what you were writing.** A note you started with **Deny with a note…** is kept for that person when their
  request changes (the card says so; sending it answers the current request) and is not carried over to anyone else.
- **Landscape with three columns** (wide landscape phones and tablets): the card sits under the terminal in the middle
  column; the right column only lists requests, and tapping one switches the terminal to that person. Turning the phone
  keeps the card you were looking at, a notification opens that person's terminal even from another tab, and once you
  answer, the terminal moves on to the next person waiting for permission.
- **Register again.** Settings → Approvals has a small **Register again** for a phone whose passkey was removed while the
  Mac still knows the phone (turned off: it registers and then turns approvals on).
- **Prefetch the next one too.** The phone fetches the sealed full text of the request on screen and
  of the next one — one at a time, because the Mac keeps one sealed request per phone — and refreshes
  it silently every 150 seconds while it is on screen.
- The audit log on the Mac now records, for each approval sent from the phone, which surface it came
  from, the card's state and how long the card was visible before it was pressed (no text, no
  command); `tools/approval_flow_stats.py` prints these as a table.
- Unchanged: what can be approved where (routine and review-tier by tap while on, dangerous by Face
  ID each time, unreadable forms on the Mac only), the signed request formats and their test vectors,
  the Mac-side checks and the hook's own re-check.

### Fixed — approving from the phone: the first stop-gaps (R116-S0)
- **Notifications land on the request.** Tapping a "Bash permission" notification now opens the approval
  screen directly instead of the session sheet; the notification reads "AI Office — <name> · Bash
  permission (now)" without emoji or the "(0 min)" suffix. The command itself is still never put in a
  notification.
- **One tap less per request.** After the Mac accepts an approval, the screen closes by itself, shows
  "✓ approved — <name> (accepted by the Mac)" in the status line for three seconds and moves on to the
  next request this phone can approve. No "Close" button to press.
- **No more silent denials.** On a session that is waiting for permission, the quick-reply buttons
  ("report progress in one line" …) are gone and the send button says **Deny & send**: a message to a
  waiting session has always been delivered as a denial, and now the button says so.
- **The relay carried a broken snapshot.** The relay agent redacted the office snapshot *in place* on a
  dictionary cached for two seconds, so the second redaction stripped the request id (`ask.id8`) and
  the phone never prefetched the full text nor bound its answer to the shown request as designed
  (R102-11); it also pushed empty syncs on every scan. Redaction now builds a new dictionary; a relay
  end-to-end pin runs the real loop and fails on the old code.
- **Texts that said the opposite of the rules.** The consent dialog shown when turning phone approvals
  on from the Mac, the notification title ("confirm the full text"), the phone-link panel, the READMEs
  and the rules said dangerous operations could only be approved on the Mac and that registration
  needed a 45-second confirmation on the Mac. They now describe the actual boundary: routine and
  review-tier by tap while on, dangerous by Face ID each time, unreadable forms on the Mac only,
  registration and switching on from the phone alone. The Mac-side "register approval key" flow,
  unused since R103-1, is hidden.
- New `tools/approval_flow_stats.py` prints counts and durations by tier, result and origin from the
  audit logs (numbers only, never text) so the next rounds can be measured against a baseline.

### Fixed — updating the hooks no longer disturbs a hook that is already waiting
- `bash hooks/install.sh` now replaces each hook by writing a temporary file next to it and renaming
  it over the old one. It used to copy over the file in place, and bash reads a running script as it
  goes: a hook that was already waiting (the delivery and approval hooks wait up to 12 hours) could
  continue from the same byte offset in the *new* file and run a fragment of it, which could turn that
  one answer into a refusal. A waiting hook now finishes on the version it started with; the next one
  starts on the new version.
- Worth knowing: a request that was already waiting before an update is still judged by the old rules.
  (A Mac-level request posted just before R118 was deployed would still refuse a one-tap allow from
  the phone, because its waiting hook keeps the pre-R118 rules. Answer such a request on the Mac.)
- If a file cannot be installed, the installer now prints ✗ and exits non-zero instead of printing ✓.
  A hook you replaced with a symbolic link stays a link.

### Changed — under the hood
- `office_json()` keeps `_lock` only around the cache; scans run under a separate single-flight lock.
  Stale results are served only while the daemon is serving (never in tests, `--dump` or the relay
  agent) and only within five seconds of the last scan. Scans that take over a second are counted
  and logged ("office の走査が遅い"). The resource summary refreshes in the background in both daemons
  (the first value after a start is `null`, as the schema already allowed).
- The timeline database gains an `events(kind, ts)` index (created once, about 0.5 s on a 170 MB
  database) so the evidence column's 24-hour query stops scanning the whole table every five seconds.
- New gates: 27 threaded tests for the cache (single flight, cold start, invalidation, failures,
  read-your-writes, the poke wait, an AST guard that only two helpers may reset the cache time), six
  timeline tests for the narrowed readers, and an acceptance bench that starts a daemon on a copy of
  the real home and measures the cold request, spaced reads, slow scans and memory.
- The cockpit subscribes to all visible terminals over **one** multiplexed stream
  (`GET /api/term/streams`), so four panes do not use up the browser's six connections per host and
  typing stays responsive. The single-terminal stream remains.
- The default arrangement is pixel-identical to beta.2 (all cockpit goldens unchanged); nothing is
  read from storage on demo or frozen-time pages.
- New gates: a layout smoke (swap, borders, persistence, keyboard), a pane smoke (splits, keys, one
  stream, persistence), real-pty pane tests including a connection-queue measurement, pure-function
  tests for the layout model and the split tree, and server tests for the multiplexed stream.

## [3.0.0-beta.2] — members' beta, 2026-10-07

*Second pre-release for the same small group of YouTube/note members. Driven by the first round of
feedback on the Mac screen ("hard to read, text cut off, panes blur together, buttons only look like
buttons on hover") and on the room ("I want to *feel* the AI working; they walk too fast").*

### Changed — the Mac cockpit, rebuilt for a real monitor
- **Layout.** The room is the hero: on a 1920×1080 window it is drawn at ×4 (736×640) with the
  **Reception** (the one request waiting for you, with its answer buttons) directly beneath it, so you
  answer right under the robot that raised its hand. The old ledger and session list are merged into a
  single **Roster** column on the right (two lines per session, nothing truncated). The terminal keeps
  the center; the compose box now sits below the terminal instead of floating over it. At 1440×900 the
  room stays at ×3 so the terminal keeps 80 columns.
- **Type.** Body text is 14px (it was mostly 11px). Settings → *Text size* offers 12/13/14/16; the
  built-in terminal follows the same setting. The invisible ANSI black in the terminal is fixed.
- **Panes.** Each pane is a framed window (1px frame, a title bar one shade up, an 8px gutter), so
  the four areas read as four things instead of one gray slab.
- **Buttons.** Every button has a visible frame and fill without hovering; primary actions are green,
  destructive ones red; the `[ label ]` bracket decoration is gone (the phone app keeps it).
- **No more cut-off text.** Project names wrap, "now" lines wrap to two lines with the full text in a
  tooltip, evidence is short (`✓ 14m`) with the full sentence in a tooltip, tab names show the full name
  on hover, and the reception keeps its buttons visible even for very long questions.

### Added — presence in the room (Mac and phone)
- **Errands grounded in what the session is doing.** Reading or grepping sends the robot to the new
  bookshelf; web fetches to the bulletin board; TodoWrite to the whiteboard; a fresh commit or passing
  test run walks it to the counter to drop a paper (which stays for ten minutes). Bash and Edit stay at
  the desk and show on the desk screen (green terminal lines, a diff, a turning page, a web page) and in
  the typing speed. Nothing is invented: every move follows the tool the session actually called.
- **Walking pace.** 24 px/s with a shorter stride (was 36 px/s), a short rise before setting off and a
  pause on arrival — slower, busier, less teleport-like.
- **Props by role.** A video project wears a director's beret, transcription a headset with a stack of
  paper on the desk, writing a pencil, research a mortarboard, infra a hard hat, and so on — only when the
  project's name or work says so; otherwise nothing.
- **Name tags** under every robot (Mac, ×3 and above), **watch mode** (`F` — just the room, full
  screen, with a ticker of what everyone is doing), and optional **ambient sound** (keys, paper, a small
  bell; off by default, Settings → *Ambient sounds*).
- The roster's "now" line gains "since …" (how long you have been watching that activity).

### Changed — under the hood
- `GET /api/office` carries `tool` (the last tool name) per session; it is not forwarded to the relay.
- New visual gates: no clipped text, every button framed, minimum type size, pane contrast, reception
  reachability, name-tag placement; a static contract test for the phone app's imports; a fourth cockpit
  golden at 1920×1080.

## [3.0.0-beta.1] — members' beta, 2026-10-04

*This is a pre-release handed to a small group of YouTube/note members for early testing, not a
public release — the date above is the day this build was cut, not the day it reached the public
repository.*

### Added

- **A third view: the Cockpit** (`?ui=cockpit`), combining a top-down pixel-art office, the
  ledger, a real terminal and a "waiting on you" list on one screen. **The Cockpit is now the
  default view on the Mac**; the 3D office is still one click away in ⚙ settings.
- **A real terminal inside the Cockpit.** You can type into it, Ctrl-C works, it resizes with the
  window, and reloading the page returns you to the same session. "+ Terminal" starts a new Claude
  Code, Codex or plain shell session in a chosen project's folder.
- **The phone now speaks the same terminal × pixel-art language as the Cockpit**, with four tabs
  (office / waiting on you / sessions / settings), a "what's next" input with suggestions, and a
  swipeable session strip.
- **Phone-side approval.** Turn "Allow from phone" on for 1, 4 or 12 hours, and a phone you
  registered with a passkey (Face ID / Touch ID) can approve requests in three tiers: an
  allow-list of routine, low-risk commands (tests, `git status`/`commit`/`push` other than
  `--force`, reads and writes inside the project) with one Face ID; anything else that can be
  fully read — including deletes, sends, scripts and settings changes — after you scroll through
  the full text and confirm, then Face ID; and requests whose risk can't be read at all
  (unreadable, too large, dynamically built commands, download-and-run) stay Mac-only. Registering
  a device and turning Allow on or off are both done from the phone alone; the Mac is notified and
  logs every change, and can revoke a device or turn Allow off instantly.
- **A place to connect AI services and APIs** (Anthropic, Mistral today), with a connection test
  and a "+ Connect" wizard on the Mac.
- **A beta entry point**: the version in the footer, a [Feedback] link, a diagnostics screen that
  shows you exactly what it collected (counts and state, never conversation text) before you copy
  it, and a Special Thanks list of beta testers who opted in to being named.
- **The terminal screen now reads like Claude Code's own transcript** — tool-and-result marks,
  diffs, a todo count, elapsed time and a "stopped because" note — instead of a raw line feed.
- **The office now shows the time of day** (window and wall light), small props on desks that hint
  at what a session is doing, a helper robot, and a red note on a desk whose session has stopped.

### Changed

- **The phone's office switched from a 3D render to the same pixel art as the Cockpit.** It loads
  roughly 0.1MB instead of roughly 1.1MB, and now works on phones that can't run WebGL.
- The phone's settings screen was reorganized around a short "current status" summary at the top
  and grouped sections below it, replacing a flatter list.

### Fixed

- **The ledger's "evidence" column was almost always empty.** Measured against real usage, it
  recognized only about 13 of 527 actual commits and 0 of several hundred passing test runs over
  three days, because it matched the shape of a command line rather than what actually happened. A
  commit is now credited by whether that project's HEAD actually moved, and a test pass by the
  test runner's own summary line, regardless of how the command was piped, quoted or wrapped.
- The phone's office could fail to load because the browser kept an old, long-cached copy of its
  files; it is now fetched from a build-numbered path so a new release always replaces it.
- Redoing a phone-approval device registration could get stuck after a cancellation; you can now
  redo it from the same link.
- Notification badges and the "is this turn open or closed, and why did it stop" state could go
  stale, and could fire while you were sitting at the Mac; both are now kept accurate and are
  suppressed while you're at the keyboard.

### Security

- **Phone approval has three tiers**: Mac-only, a remote allow-list, and "read the full text and
  confirm" — and a request whose risk category can't be fully determined from its text is always
  pushed to the Mac-only tier, never offered to a phone.
- Closed a gap where two approval requests arriving close together could let an "allow" meant for
  one apply to the other, shown in its place.
- Closed a gap in the first phone-approval rollout that could have let a `git push` to a public
  repository, or an arbitrary write inside the repository, through the remote-approval path.
- A permission prompt's full text, and a project name not on the relay's allow-list, were each
  reaching the relay in one narrow path; both are now withheld.

## [2.1.0] — 2026-09-19

### Fixed

- **The office now starts on a Mac that has no Homebrew Python.** Two type annotations used
  syntax from Python 3.10, so on the stock `/usr/bin/python3` (3.9.6) the server, the MCP server,
  the relay agent and the timeline all failed to import — the app did not start at all. A gate now
  boots the server with the stock interpreter on every run, so this cannot come back silently.
- **The relay usage gauge was counting a fraction of what it should.** Only one of eight write
  paths was measured, so a phone polling over HTTP consumed about 4,300 rows a day without
  appearing in the gauge that promises "we slow down automatically above 80%". Every write path is
  now counted, and the phone's presence marker is written once a minute instead of on every poll.
- The signature and Web Push known-answer tests, and the four Mac-versus-phone parity tests, now
  run in the default verification pass. They needed no extra tooling, but sat behind an optional
  flag, so a change that broke the phone's signature could pass every gate.

### Added

- **A second view: the ledger** (`?ui=pixel`, experimental). One row per project, thirty pixels
  tall, so twenty projects and forty sessions fit on one screen; a ❗ opens its session rows and
  sits at the top, and the number keys answer it exactly as they do in the 3D office. Switch
  between the two views in ⚙ settings. The 3D office stays the default and is unchanged.
- **An evidence column in the ledger.** Each row says whether that session actually landed a
  commit or passed a test in the last 24 hours, taken from what the event hook recorded rather
  than from what the session says about itself. A row with no record reads "—" and explains why,
  and a command whose exit status does not belong to the test — piped into `tail`, followed by
  `|| true`, run in the background — is never counted as evidence.
- Issue templates, a security policy and a support pointer, so there is somewhere to go when
  something breaks.

## 2.0.0 — 2026-09-16

*Released before this project cut tagged releases, so there is no `v2.0.0` tag to link to.*

The office became a place rather than a diagram.

### Added

- **Furniture and robots are real 3D models.** Desks, chairs, sofas, meeting tables, counters,
  shelves, plants and phone booths are generated models baked to vertex colours; the robots are
  skinned characters with an animation set, so they walk, sit, type, look around and celebrate.
- **Four meeting rooms and a lounge.** Sub-agents appear as small robots at the table of the
  session that spawned them.
- **A calmer, brighter room.** White, pale lavender and glass in cool daylight, with monitors that
  glow on their own.
- **Answering got honest about time.** A reply that has not landed after three minutes says so and
  offers to send again, instead of implying it arrived.
- **One vocabulary for delivery.** The Mac and the phone use the same five words for whether an
  instruction is live, saved, pending, stalled or offline.
- **Ways back into a question.** Deep links open the ❗ that a notification was about, the tray
  can be walked with the keyboard in both directions, and `?` shows the key map.

### Fixed

- **The ghost ❗.** An approval prompt that you answered in the terminal could leave the office
  showing the question for hours. The notice now has a heartbeat, so a notice whose process is
  gone is treated as gone within seconds.
- Gestures that disappeared when the robots became 3D models — the raised hand for a question,
  the nod after an approval, typing at a desk, hands on the console, and the meeting robots'
  nodding and hopping — are back.
- Updating one UI file no longer makes every phone re-download the whole bundle (about 1 MB).

[Unreleased]: https://github.com/senao-routine/ai-office/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/senao-routine/ai-office/releases/tag/v2.1.0
