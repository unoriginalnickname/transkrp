# 0004. Detect punctuation, never infer it

- Status: Accepted
- Date: 2026-07-16 (recorded 2026-07-26)

## Context

Paragraph breaking needs to know whether the track has sentence punctuation to
break on. The tempting shortcut is to derive it from the track's source: manual
captions are written by humans and punctuated; auto-captions are ASR output and
aren't.

Both halves of that are false often enough to matter:

- YouTube's newer ASR emits punctuation *and* `>>` speaker markers. A recent
  auto-caption track reads like prose.
- Plenty of manual tracks are unedited dumps — a volunteer pasting an ASR export,
  or a broadcaster's stenographic feed in all caps with no full stops.

Getting it wrong is not cosmetic. If we assume punctuation that isn't there, the
"wait for a sentence end" rule never fires and paragraphs run to the hard cap
every time — 160-word walls of unbroken text.

## Decision

Detect it from the text. `is_punctuated` counts the fraction of segments ending
in `.`, `!` or `?` (allowing a trailing quote or bracket) and returns true at 5%
or more. The result is reported in the output frontmatter as `punctuated:`.

The threshold is low on purpose: cues are cut every few seconds regardless of
sentence boundaries, so even in fully punctuated prose most segments end
mid-sentence. 5% means "sentence endings occur at all", not "most segments are
sentences".

## Consequences

- Both mixed cases work: a punctuated auto-track gets sentence-aware breaking, an
  unpunctuated manual track gets length-capped breaking.
- The reader is told which one they got. `punctuated: false` also emits a `note:`
  in the frontmatter, because an unpunctuated transcript is a different kind of
  document — you cannot quote a sentence from it.
- A track with a handful of stray full stops in otherwise raw ASR will read as
  punctuated and break badly. The cap still bounds the damage, and in practice
  the two populations are far apart — real ASR output has ~0%.
