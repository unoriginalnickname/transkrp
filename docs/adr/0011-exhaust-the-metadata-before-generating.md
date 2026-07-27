# 0011. Exhaust the metadata before generating anything

- Status: Accepted
- Date: 2026-07-27

## Context

Dogfooding a 40-video interview playlist produced three complaints about the
output, all real: 18 of 30 transcripts were unpunctuated walls of text, none of
30 had speaker attribution, and every episode opened with an identical sponsor
read.

The obvious answers were all *generative*. Restore punctuation with an LLM.
Infer speaker labels with an LLM. Diarize the audio with a model. Each would
have worked, and each would have cost the same thing: the output stops being
what was said and becomes what a model thinks was said.

That cost is not incidental to this tool, it is aimed at its one load-bearing
claim. Every paragraph carries a timestamp *so that anything important can be
checked against the audio*. Restored punctuation asserts sentence boundaries the
speaker may not have made. Inferred speaker labels are precisely the guessing
[ADR 0005](0005-turns-not-speakers.md) refused. A reader cannot tell which parts
of a half-generated document are verbatim, so in practice none of it is.

Then, while looking for something else, `chapters` turned up in the metadata we
already fetch and immediately discard — 19 human-written section titles for the
33,000-word transcript that had prompted the "unreadable wall" complaint. The
creator wrote them. We had them in hand the whole time.

## Decision

**Exhaust what the source already tells us before generating anything.** In
order of preference:

1. Data already in the response we fetch (chapters, uploader, upload date,
   duration, the video id).
2. Data derivable from it with no judgement (a timestamp becoming a link).
3. Only then, and reluctantly, something generated — and never silently.

Three things followed immediately, none of which invent a word:

- **Chapters become `##` headings.** The document had structure; we were
  dropping it.
- **Timestamps become links** to `&t=<seconds>s`. "Checkable against the audio"
  is a different proposition at one click than at a manual scrub to 1:04:50.
- **Channel and upload date go in the frontmatter.** A knowledge file that
  doesn't say who said this or when is a quote with the attribution torn off.

## Consequences

- The unreadable-wall complaint is substantially answered without generating a
  syllable: 279 undifferentiated paragraphs became 19 titled sections.
- The fix costs nothing — no API key, no model, no second network call, no
  per-video cost, no new failure mode. It was one extraction away the whole time.
- Not every video has chapters. Roughly three in four of the ones sampled did;
  the rest render exactly as before.
- **This is not an argument that generative post-processing is worthless.** It is
  an argument about order. Punctuation restoration remains the honest answer to
  raw ASR, and if it is ever added it belongs behind a flag, in a separate field,
  with the frontmatter saying so — not folded into text that claims to be a
  transcript.
- The general lesson is cheaper than the specific one: when the output feels
  poor, look first at what the source already said and you threw away. The
  interesting features here were `chapters` and `uploader`, sitting in a dict we
  had already paid for.
