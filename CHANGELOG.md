# Changelog

All notable changes to AI Office, written for the people who run it rather than for the people
who wrote it. Dates are the day the change reached the public repository.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The version you are running is printed
by `python3 server/office_version.py` and returned by `GET /api/office` as `app.version`.

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
