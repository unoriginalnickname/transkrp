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

## Amendment, 2026-07-27: three subclasses

The revisit above happened, twice — first when batch runs needed to tell a block
from an ordinary failure, then when a library user would need to tell "skip this
video forever" from "try another track". There are now three subclasses, all of
`LookupError`, so the contract above is unchanged for anyone who doesn't care:

| Raised | Means | The caller's move |
|---|---|---|
| `RateLimited` | refused for volume or IP reputation | wait, or use `--proxy` |
| `Unavailable` | private, deleted, region-locked, age-gated | skip it permanently |
| `NoCaptions` | video is fine, no usable track | try `--lang`, or skip |
| `LookupError` | anything else — network, malformed payload | retry, or report |

Classification is regex over yt-dlp's free-text messages, which will drift. It
only decides whether a batch run gives up, skips, or carries on, so a miss costs
wasted requests rather than a wrong transcript.

**This is what found the age-gate bug.** `_BLOCKED` matched `sign in to confirm`,
and YouTube says *"sign in to confirm you're not a bot"* for a block but *"sign
in to confirm your age"* for an age gate. Every age-restricted video was
therefore classified as a rate limit — which, once batch runs learned to abort on
`RateLimited`, meant one age-gated video in a playlist killed the other 199 and
told the user to wait out a block that was not happening. The shared prefix is no
longer a signal, and an age gate now names `--cookies` as the fix.
- Messages are tested by matching on the actionable phrase, so rewording one
  breaks its test on purpose.
- YouTube rate-limits caption pulls per IP at empirically a few hundred an hour
  and blocks datacenter ranges outright. That is why batch runs sleep a second
  between videos and why `--proxy` exists — from a laptop neither matters, from a
  cloud box the tool is unusable without them.
