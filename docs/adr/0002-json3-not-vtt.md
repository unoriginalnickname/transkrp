# 0002. Read json3, not vtt

- Status: Accepted (with a correction, 2026-07-26 — see the end)
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

## Correction, 2026-07-26

The decision stands; the mechanism described above was wrong, and it was wrong in
a way that made the code look more load-bearing than it is.

Trying to prove the live smoke tests could actually detect a dedup regression, I
disabled the `aAppend` filter and the tests still passed. They were right to:
**disabling it changes nothing.** Measured across three videos — 1371 of 2744
events on the hour-long lecture, 51 of 104, 499 of 1000 — *no `aAppend` event
carries a single word.* Every one holds exactly `"\n"`, so the `if text:` guard
downstream already drops them.

So json3 does not "flag the duplicate text for removal". json3 **never emits the
duplicate text**. The scroll animation is expressed as an append of a newline to
the existing window, where `.vtt` re-serialises the whole visible box every time
it changes. The 14,514-vs-4,884 word gap is real, but it is a fact about `.vtt`,
not a repair we perform on json3.

What follows:

- Choosing json3 over vtt is still right, and for a stronger reason than the one
  originally written down: the duplication is absent by construction rather than
  removed by a filter we have to keep correct.
- The `aAppend` check stays anyway. It costs nothing, it documents what the field
  means, and a track that ever did put words in an append would otherwise
  duplicate silently. It is a guard, not the mechanism.
- The claim that "keeping them triples the transcript" is false for json3 and has
  been corrected in the code comment and the README.
- Generalise carefully from three videos. That sample is enough to know the
  filter is inert today, not enough to know YouTube will never use `aAppend`
  differently — which is exactly why the guard is cheap insurance.
