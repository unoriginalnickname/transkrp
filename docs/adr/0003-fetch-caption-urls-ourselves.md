# 0003. Fetch the caption URL directly

- Status: Accepted
- Date: 2026-07-16 (recorded 2026-07-26)

## Context

yt-dlp can download subtitles itself (`writesubtitles`, `subtitlesformat`). Using
it would mean asking yt-dlp to write a file to a temp directory, reading it back,
and deleting it — to obtain bytes we could have held in memory.

It would also mean hitting yt-dlp issue #10360, where writing a `json3` subtitle
raises `_UnsafeExtensionError` because `de.json3` isn't on the safe-extension
list. That bug is about disk, not the network.

`extract_info` already hands us the fully-formed caption URL in
`subtitles[key][i]["url"]`. Fetching it is one request.

## Decision

Take the URL from the extraction result and fetch it with `urllib` in `_get`.
Never ask yt-dlp to write a subtitle file.

## Consequences

- No temp files, no cleanup, no `_UnsafeExtensionError`.
- **These URLs are signed, IP-bound and time-limited.** They carry `ip`,
  `ipbits`, `expire`, `signature` and `sparams`. Two things follow: the fetch has
  to happen from the same machine that did the extraction, and the transcript
  dict must not be treated as something you can re-fetch later. A stale or
  wrong-IP URL comes back 403, which `_get` reports as "it expired or was issued
  for a different IP".
- We are outside yt-dlp's networking stack, so its proxy, headers and retry
  settings do not apply to this request. `_get` therefore carries its own
  User-Agent, its own timeout, its own backoff, and `--proxy` is plumbed into
  *both* the extraction and the fetch. Forgetting the second one would produce a
  tool that appears to support proxies and still gets IP-blocked.
- We must handle a failure mode yt-dlp would have handled: when the timedtext
  endpoint wants a PO token it doesn't have, it answers **HTTP 200 with an empty
  body** (yt-dlp issue #13075). That is not an error status, so it has to be
  checked for explicitly — otherwise `json.loads` dies with "Expecting value:
  line 1 column 1", which tells the user nothing about tokens.
