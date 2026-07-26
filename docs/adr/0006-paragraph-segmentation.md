# 0006. Break on turn, then silence, then length

- Status: Accepted
- Date: 2026-07-16 (recorded 2026-07-26)

## Context

Caption cues are three seconds long and cut mid-sentence. A transcript that keeps
them is unreadable; a transcript that joins them all is a wall. Something has to
decide where paragraphs end.

The available signals, in descending order of how much they mean:

1. **A speaker change** (`>>`) — always a boundary. Nothing else competes.
2. **A silence.** A two-second gap in speech is a topic break; anything shorter
   is a breath.
3. **Length.** Meaningless as a signal, but a bound is needed when the other two
   don't fire — which, on an unpunctuated lecture, is for forty minutes.

Two failure modes to avoid. Break only on length and dialogue turns into
arbitrary blocks that straddle speakers. Break on every silence and every
"Right." becomes its own paragraph.

## Decision

In `paragraphs()`, in order:

- **Speaker change always breaks**, however short the turn was. A one-word answer
  is its own paragraph — that is the exchange, not an artifact.
- **A silence of `GAP_BREAK_MS` (2s) breaks, but only past `MIN_WORDS` (25).**
  Below that the pause is ignored, so a short utterance before a silence joins
  what follows instead of being stranded alone.
- **Length caps it.** With punctuation, `TARGET_WORDS` (110) is where we *start
  looking* for a sentence end and `2 × TARGET_WORDS` is the hard cap. Without
  punctuation there is nothing to wait for, so the target is the cap — a loose
  one there produces the 160-word walls this is meant to prevent.
- Paragraphs consisting only of `[Music]` or `[Applause]` are dropped. The tag is
  worth keeping inline; it is not worth a paragraph.

`--words N` moves the target.

## Consequences

- Output reads as prose, with a `[timestamp]` per paragraph so any line traces
  back to the video.
- 110 words is a *reading* unit, roughly 150 tokens. RAG guidance suggests
  300–600 token chunks, which is why the target is a parameter: `--words 250`
  produces chunk-shaped paragraphs from the same source. Paragraphs are not
  chunks and this tool does not pretend to be a chunker — a consumer that wants
  overlap or semantic grouping has `start_ms` and `turn` on every paragraph to
  do it with.
- The constants are tuned by eye against lectures and interviews, not derived.
  They are module-level and named for that reason.
- No semantic segmentation. Detecting an actual topic shift needs a model; this
  is a deterministic, offline, sub-second transform and the trade is deliberate.
