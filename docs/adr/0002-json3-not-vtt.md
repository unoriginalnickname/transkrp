# 0002. Read json3, not vtt

- Status: Accepted
- Date: 2026-07-16 (recorded 2026-07-26)

## Context

YouTube serves the same caption track in several formats: `vtt`, `ttml`, `srv1`,
`srv2`, `srv3`, `json3`. Everything written about yt-dlp recommends `vtt` — it's
the default, it's a standard, every tool reads it.

For manual captions the format barely matters. For auto-captions it decides
whether the output is correct.

Auto-captions are rendered as a rolling two-line box: a phrase appears on the
bottom line, then moves to the top line while the next phrase appears below. The
`.vtt` serialisation of that animation repeats each phrase in every frame it is
visible for. Strip the timestamps and you get every phrase two or three times.
It is invisible unless you count: **a naive `.vtt` strip produced 14,514 words
against this tool's 4,884 on the same talk.**

Deduplicating after the fact is guesswork — a speaker who genuinely repeats
themselves is indistinguishable from the scroll artifact. Tools exist that try
(`srt_fix`, `webvtt-to-json --dedupe`) and they are heuristics.

`json3` doesn't need the guess. It is the format the player itself consumes, and
it marks the scroll re-emissions explicitly: those events carry `"aAppend": 1`
and a body of just a newline. Dropping them is exact, not statistical.

## Decision

Read `json3`. Skip any event with `aAppend == 1` or no `segs`.

## Consequences

- Deduplication is exact. There is no threshold to tune and no false positive on
  a speaker who repeats themselves.
- We also get `tStartMs` and `dDurationMs` as integers instead of parsing
  timestamp strings, and no HTML entities to unescape.
- `json3` is undocumented and could change shape. The `wireMagic: "pb3"` envelope
  suggests it is a JSON projection of a protobuf, so field names are more stable
  than they look — but a missing `events` key is handled as "no segments" rather
  than a crash.
- **Do not "fix" this by switching to vtt.** Blog posts from 2026 recommend
  exactly that, citing yt-dlp issue #10360, where `--sub-format json3` raises
  `_UnsafeExtensionError`. That bug is in yt-dlp's *file writing* — it refuses to
  create `de.json3` on disk. We never ask yt-dlp to write a file (see
  [0003](0003-fetch-caption-urls-ourselves.md)), so it does not apply here.
