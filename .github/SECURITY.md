# Security

AI Office runs entirely on your Mac. The HTTP server binds `127.0.0.1` only, and the project
operates no server of its own — there is nothing of yours for the author to see. The two places
where data can leave your machine are documented in the README ("What leaves your Mac"):
the Cloudflare Worker **you** deploy for the phone relay, and the cost gauges that read **your**
provider balances with **your** API keys.

## Reporting a vulnerability

Please **do not open a public issue** for anything that could be exploited. Instead, use
GitHub's private vulnerability reporting on this repository:
**Security → Report a vulnerability**. It reaches me directly.

Tell me what you found, how to reproduce it, and what an attacker would gain. A proof of
concept is welcome but not required.

This is a one-person project, so I cannot promise a response time. I will acknowledge
what I receive and say plainly whether I intend to fix it, mitigate it, or document it as
an accepted limitation.

## Scope

In scope: the local server and its API, the delivery and approval hooks, the signed-envelope
protocol between the phone and the Mac, the relay Worker, the sealed-dialog encryption, and
the remote-execution allowlist.

Out of scope: anything that requires an attacker to already have a shell on your Mac, and the
security of Claude Code, Codex CLI or Cloudflare themselves.

## Known and accepted limitations

These are deliberate, documented trade-offs rather than bugs:

- **Permission decisions are local-only by design.** Only the person at the Mac (`src="local"`)
  can allow a tool call. Replies that arrive through the relay are structurally downgraded to
  deny-with-a-message.
- **The relay never receives message bodies.** The office payload that leaves your Mac is an
  allowlist, not a redaction pass, so new fields are excluded by default.
- **A paired phone can reach every project.** There is no per-device project scope yet; pairing
  a device trusts it with the whole office. Revoke devices you no longer use.
