# 0007. Every failure is a LookupError with a next step

- Status: Accepted
- Date: 2026-07-26

## Context

Everything this tool can fail at is someone else's decision: the video is
private, the video has no captions, YouTube is rate-limiting this IP, the signed
URL expired, the request needs a PO token. None of it is a bug in the caller's
code, and none of it is fixed by a stack trace.

The failures also arrive in unlike shapes — `yt_dlp.utils.DownloadError`,
`urllib.error.HTTPError`, `json.JSONDecodeError`, a bare `TimeoutError`, and a
200 response with an empty body that isn't an exception at all. A caller
importing `transcript()` should not have to know that list.

Retrying is not uniformly right either. A 429 or a stalled socket will likely
work in ten seconds. A DNS failure will not, and retrying it spends twelve
seconds to print the same message.

## Decision

**One exception type.** Everything recoverable-by-the-user is raised as
`LookupError` with a message naming the next action. `transcript()` raises
nothing else for a failure the user can act on.

**Retry only what is transient.** 429 and 5xx, timeouts, and connection resets
retry with exponential backoff plus jitter (`2s, 4s, 8s`, ±25%). HTTP 4xx other
than 429, DNS failures and TLS refusals raise immediately. Jitter is there so
concurrent runs don't re-collide on every retry.

**Name the fix in the message**, not just the fault:

| Condition | Message says |
|---|---|
| 429 after retries | wait a few minutes, or use `--proxy` |
| 403 | the URL expired or was issued for a different IP |
| 200 + empty body | it usually needs a PO token; upgrade yt-dlp |
| `--lang` miss | which tracks the video actually has |
| no English track | which tracks exist, and to pass `--lang` |
| no tracks at all | that the video has no captions, full stop |

**A batch run does not abort.** One private video in a playlist must not cost the
other 199. Per-video errors go to stderr and the run continues; the exit code is
1 if anything failed.

## Consequences

- `except LookupError` is the whole error-handling contract for a library user.
- It is a slightly odd choice of builtin for "network failed". The alternative is
  a custom exception hierarchy, which is more correct and makes the one-line
  library call worse. Revisit if callers ever need to distinguish "unavailable"
  from "rate-limited" programmatically.
- Messages are tested by matching on the actionable phrase, so rewording one
  breaks its test on purpose.
- YouTube rate-limits caption pulls per IP at empirically a few hundred an hour
  and blocks datacenter ranges outright. That is why batch runs sleep a second
  between videos and why `--proxy` exists — from a laptop neither matters, from a
  cloud box the tool is unusable without them.
