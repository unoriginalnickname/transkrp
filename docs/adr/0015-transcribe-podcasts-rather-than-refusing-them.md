# 0015. Transcribe podcasts ourselves, rather than refusing them

- Status: Accepted
- Date: 2026-08-06

## Context

Every other decision in this project rests on a fact that stops being true here:
YouTube has already done the speech recognition. `json3` versus `vtt`
([ADR 0002](0002-json3-not-vtt.md)), track selection, punctuation detection
([ADR 0004](0004-detect-punctuation.md)) — all of it is *processing somebody
else's transcript*. Podcasts mostly have no transcript at all.

That gap was found by hitting it. An episode of The Valued Cultures Podcast was
linked from Amazon Music, and the answer was a chain of dead ends: yt-dlp refuses
Amazon Music outright (`[DRM] The requested site is known to use DRM
protection`), and it is right to. The route that worked was found by hand, run by
hand, and then lost — the resulting transcript sat in the working directory for
nine days as the only evidence any of it had happened, while the tool it belonged
to could not do it.

So the question was not "should this feature exist" — it had already been used.
It was whether the capability belongs *in* a project whose entire character is
one runtime dependency and no audio processing.

## Decision

**Podcasts are in, and speech recognition is the one thing here that isn't
optional-by-omission.** A person with a podcast link wants a transcript, and
"use a different tool, then come back" is a worse answer than a large optional
dependency.

**A storefront link is a dead end, and the error says so instead of guessing.**
Amazon Music and Spotify episode pages were both fetched during this work:
Amazon's returns 11KB of JavaScript shell with no `<title>` and no `og:title`;
Spotify's serves a generic "Spotify — Web Player". There is no metadata to
recover, so scraping them is not a fallback, it is a bug that reports the wrong
show. They raise an error naming the show as the way in.

**The iTunes Search API resolves a show name to a feed, and the Lookup API
resolves an Apple link exactly.** No key, no auth, no scraping. An Apple URL
carries the numeric id already, so it is looked up rather than searched — a
fuzzy match on a show name is a real risk of the wrong podcast, and there is no
reason to take it when the id is right there.

**A published transcript wins over ASR.** Podcasting 2.0 defines
`<podcast:transcript>`, and when a feed has one it is free, exact, and
intentional. Most don't — the feed this was built against declares the namespace
and never uses the element — but the check costs one regex against minutes of
CPU, and reconstructing badly what is already published would be absurd.

**A show means its latest episode, not all of them.** This inverts the playlist
rule from [ADR 0008](0008-playlists-and-multiple-urls.md), deliberately. A
YouTube playlist expands because each entry costs one caption fetch. Each podcast
episode costs a *whisper run* — the 21-episode feed here is roughly fourteen
hours of CPU. Expanding that because somebody pasted a show name is not a
default, it is a trap. `--playlist` still takes them all, and which reading was
used is printed either way.

**`vad_filter=True` and `condition_on_previous_text=False` are not tuning.**
Both differ from faster-whisper's defaults, and both prevent the same failure:
whisper's loop, where one hallucinated phrase conditions the next window and
repeats for minutes. The `repetition()` canary that already existed for
yt-dlp's caption scroll catches it, because statistically the two are the same
artifact.

**The document says it is ASR, and says what made it.** `source: whisper`, a
`model:` line, and a note that names in particular need checking against the
audio. `small` handles conversational English well and proper nouns badly — the
guest's studio came out mangled while ordinary prose was clean — and proper
nouns are exactly what a corpus of people is built from
([ADR 0011](0011-exhaust-the-metadata-before-generating.md) is the same
principle: generated content is labelled and kept out of the verbatim text).
Unlike an unpunctuated auto-caption track, whisper output *looks* clean, so
nothing about the file itself would warn a reader. The frontmatter has to.

## Consequences

The output is the same shape as every other document here, so `graph.py`,
`ontology.py` and `--speakers` work on podcasts without knowing they are
podcasts. That was the point of routing whisper's ~15-word utterances through
`transkrp.paragraphs()` rather than writing them out directly.

`faster-whisper` is a `[podcast]` extra, imported inside the one function that
transcribes. Finding a feed, listing episodes and picking one need nothing beyond
the standard library, so `--list` works on a core install; only transcription
pulls in CTranslate2, `av`, `tokenizers` and `onnxruntime`. `podcast.py` ships in
`py-modules` alongside `sponsors` for that reason.

Timestamps link to the audio with `#t=<seconds>`, which meant `_at()` had to stop
assuming YouTube. Without it every citation in a podcast document pointed at the
top of the same hour-long file — a citation that doesn't resolve, which
[ADR 0013](0013-a-degraded-graph-must-not-pass-for-a-finished-one.md) argues is
worse than none.

The verification for this was reproducing the hand-made transcript: the same
episode, through the committed code, produced **77 of 77 paragraphs identical**
in both timestamps and text. The ad-hoc run is now reproducible, which is the
thing it wasn't.

Costs accepted: a third network dependency (the iTunes directory), the first
audio decoding in the project, and a runtime measured in minutes rather than
seconds. The last one is why progress is printed at all — an hour of audio spends
several minutes in whisper with nothing else to show.
