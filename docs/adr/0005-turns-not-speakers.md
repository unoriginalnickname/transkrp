# 0005. Mark turns, don't name speakers

- Status: Accepted
- Date: 2026-07-16 (recorded 2026-07-26)

## Context

A two-hour interview transcript without speaker attribution is much less useful
than one with it. The obvious wish is `SPEAKER A:` / `SPEAKER B:` labels, and the
RAG literature is unanimous that chunks should carry a `speaker` field.

The caption track does not contain that information. What it contains is `>>`,
the broadcast captioning convention meaning "a different person is talking now".
It marks the *boundary*. It does not say who, and it does not say that the two
`>>` turns thirty minutes apart are the same person.

Producing labels anyway would mean either:

- **Guessing** — alternate A/B/A/B. Wrong the moment a third person speaks or
  someone takes two consecutive turns, and wrong invisibly.
- **Diarizing the audio** with something like pyannote. That is a real answer,
  and a different tool: it means downloading the audio, a model dependency, and
  minutes of GPU time per video instead of one second of network.

A wrong speaker label is worse than no speaker label. It is a claim about who
said something, and someone will quote it.

## Decision

Track turn boundaries and nothing else. `_split_turns` splits segments on `>>` so
a speaker change never sits mid-paragraph; each paragraph carries an integer
`turn`; markdown output re-emits `>>` at each change; frontmatter reports the
turn count with the comment "who is speaking is not marked".

## Consequences

- Dialogue structure survives — you can see the shape of an exchange, and a
  one-word answer stays its own paragraph
  (see [0006](0006-paragraph-segmentation.md)).
- Attribution does not survive, and the output says so rather than leaving the
  reader to assume.
- `turn` is an integer index, so a consumer that *does* run diarization can join
  its labels onto our turns rather than re-segmenting.
- Tracks without `>>` — most single-speaker lectures, and manual tracks that use
  `NAME:` prefixes instead — come out as one turn. The `NAME:` prefixes are left
  in the text, where they are readable and greppable, but they are not parsed
  into structure. Parsing them would be a guess about a free-text convention.
