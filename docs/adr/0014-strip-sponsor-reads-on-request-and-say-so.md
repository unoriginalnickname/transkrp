# 0014. Strip sponsor reads on request, and say so in the document

- Status: Accepted
- Date: 2026-07-28

## Context

Dogfooding a 30-episode interview playlist surfaced this: every transcript opens
with a sponsor read — "huge thank you to our sponsor… discount code…". For a
document somebody reads, that is mild noise. For the thing this corpus is
actually for, it is the worst possible noise, because it is *near-identical
across every episode of a series*. A retrieval index over thirty episodes gets
thirty near-duplicate passages that match any query about the sponsor's product
and nothing the episodes are about. Repetition is what makes a passage look
salient, so the noise is boosted precisely because there is a lot of it.

The timings are already crowd-sourced. SponsorBlock has them, and yt-dlp
already speaks to it, so the work is dropping cues rather than finding segments.

Two things made this worth an ADR rather than a patch. It is a second network
dependency, on a service with no relationship to YouTube. And it means a
document called a transcript would no longer contain everything that was said.

## Decision

**Opt-in, via `--strip-sponsors`.** The default output stays a faithful record of
the audio. A tool that silently removes content from a document labelled
"transcript" has broken the one promise the format makes, and the breakage is
invisible — the reader has no way to tell a video with no sponsor from a video
whose sponsor was cut. Retrieval quality is a good reason to want the cut; it is
not a good reason to make it the unrequested default.

**And the document declares the cut.** When segments are removed the frontmatter
gains a `sponsors_removed` line per span:

```yaml
sponsors_removed: 00:00-01:48
sponsors_removed: 12:03-12:51
```

This is the part that makes the feature acceptable rather than merely useful. An
incomplete document that says where it is incomplete is still evidence; a reader
who finds a jump at 01:48 can see why, and can refetch without the flag. It is
the same principle as the `speakers_flagged` lines from
[ADR 0012](0012-validate-the-model-against-a-domain-model.md): what the tool did
to the text travels with the text.

**The privacy-preserving endpoint, not the direct one.** Querying
`?videoID=<id>` tells sponsor.ajay.app exactly which video you are transcribing.
`GET /api/skipSegments/<sha256(videoID)[:4]>` returns every video whose hash
shares those four hex characters — 55 videos when measured — and we filter
locally. The leak is reduced to a 4-character prefix, the response is one request
either way, and it costs one filtering step. yt-dlp made the same choice.

**A 404 means "no segments", not an error.** Measured 2026-07-28: the API returns
404 both for a video nobody has submitted segments for and for a nonsense video
ID. There is no distinguishing them, and the common case by far is the former. So
404, a timeout, a malformed body, and an unreachable host all resolve to "no
segments" and the transcript proceeds intact. **This feature must never be able
to fail a fetch.** It is an enhancement to a document that is perfectly good
without it, and a hard dependency on a third-party service for that would be a
bad trade — see [ADR 0007](0007-failure-handling.md) on failures earning their
loudness.

**`sponsor` only, not the other categories.** SponsorBlock also classifies
`selfpromo`, `interaction` ("like and subscribe"), `intro`, `outro`, `filler`.
Those shade into content — a creator plugging their own project is often
discussing the subject of the video. `sponsor` is the category with a crisp
definition and the one the complaint was about.

**Cut cues, before paragraphs are built.** Segmentation runs on what survives, so
paragraphs never straddle a removed span and timestamps stay true to the video. A
cue counts as inside a sponsor span when its *midpoint* is, which handles the cue
that straddles a boundary without needing a fractional-overlap threshold.

**Refuse a cut that would eat the video.** SponsorBlock's data is crowd-sourced
and therefore occasionally wrong or vandalised, and a single mis-submitted
segment can span the whole runtime. If the segments would remove more than half
the cues, none are removed and nothing is recorded. A transcript degraded to
nothing is a worse outcome than one sponsor read left in, and the asymmetry is
sharp enough to hard-code rather than expose as a threshold.

## Consequences

- The default behaviour of the tool does not change at all. Nobody who has not
  asked for this gets it.
- Coverage is whatever SponsorBlock has, which is good on large channels and
  thin on small ones. A video with no submissions is silently unchanged — correct
  behaviour, and indistinguishable from a video with no sponsor. The
  `sponsors_removed` lines are the only way to tell, which is another reason they
  are there.
- **The cut is only as accurate as the crowd's timings, and the first real video
  tried showed the limit.** Veritasium `tL9Lw250spc`: SponsorBlock's span is
  33:41–35:24, and the paragraph at 33:44 opens as the video's conclusion —
  "how metabolic rate scales with mass has been one of biology's biggest debates
  for centuries" — before turning into the ad two sentences later ("And today's
  sponsor, Brilliant…"). The read *begins inside a content paragraph*, so cutting
  the span loses those two sentences.

  Cue-level granularity does not save this and nothing else would: the segment
  boundary is the only information available, and it says 33:41. Cutting
  mid-paragraph on a guess about where the ad "really" starts would be inventing
  a boundary the data does not have. The honest answer is that the span is
  declared in the frontmatter, so a reader who wants the conclusion knows exactly
  where it went. This is the strongest argument for the flag being opt-in.
- A second network dependency, but a soft one: it can be down without anything
  breaking, and it is only contacted when the flag is passed.
- Not applied to `--speakers` or the graph as a separate concern — they consume
  paragraphs, so they get the stripped text automatically when the flag is on.
