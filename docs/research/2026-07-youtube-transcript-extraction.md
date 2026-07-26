# How YouTube transcript extraction actually works

*Surveyed 2026-07-26. Everything below was checked against the live API on that
date unless marked otherwise; several published claims turned out not to survive
the check.*

The decisions this fed into are in [../adr/](../adr/README.md). This document is
the survey, not the conclusions: what the landscape looks like, what was
measured, and where the received wisdom is wrong.

---

## Four ways to get a transcript

| Approach | Verdict |
|---|---|
| **YouTube Data API v3** | Cannot do it. `captions.list` names the tracks; `captions.download` only works for videos **you own**. Quota'd at 10,000 units/day, 200 per caption call. Useless for someone else's video, which is the entire task. |
| **InnerTube directly** | What most blogs and commercial scrapers do. POST `/youtubei/v1/player` impersonating `clientName: "ANDROID"`, scrape `INNERTUBE_API_KEY` out of the watch page, read `captions.playerCaptionsTracklistRenderer.captionTracks`, fetch each track's `baseUrl`. Works, and is a moving target: client versions, key extraction, signature scheme and bot checks all change without notice. |
| **youtube-transcript-api** | Wraps the same endpoint. Actively maintained, good ergonomics, extensive proxy support. Returns **no video metadata** — no title, no duration. |
| **yt-dlp** | Wraps the same endpoint plus a decade of accumulated repairs, and returns full metadata. Chosen. |

Measured against youtube-transcript-api on a test video: **676 segments, 0
differences** in segment text. The two produce identical output. The deciding
factor was metadata, not correctness.

## The timedtext endpoint

Caption tracks are served from `/api/timedtext`. The URL yt-dlp hands back looks
like:

```
?v=…&ei=…&caps=&opi=…&xoaf=…&hl=en&ip=0.0.0.0&ipbits=0&expire=…
&sparams=ip,ipbits,expire,v,ei,caps,opi,xoaf&signature=…&key=yt8&lang=en&fmt=json3
```

Three things follow from those parameters, and all three shaped the design:

1. **`ip` and `ipbits` are inside `sparams`, and `sparams` is what `signature`
   covers.** The URL is bound to the IP that requested it. Extract on one host
   and fetch on another and you get a 403.
2. **`expire` is a unix timestamp a few hours out.** A transcript dict is not
   something you can stash and re-fetch later.
3. **`fmt` selects the serialisation.** `json3` is the player's own format.

### Formats

`vtt`, `ttml`, `srv1`, `srv2`, `srv3`, `json3`. Everything written about yt-dlp
recommends `vtt`; for auto-captions that recommendation is actively harmful.

Auto-captions render as a rolling two-line box. `.vtt` re-serialises the whole
visible box every time it changes, so each phrase appears in every frame it's
visible for — **14,514 words against 4,884** for the same talk, every phrase two
or three times, invisible unless you count.

`json3` expresses the same animation as an event with `"aAppend": 1` carrying a
body of `"\n"`. The text is not repeated.

## Two published claims that don't hold

**"json3 is broken in 2026, use vtt."** Widely repeated, citing
[yt-dlp #10360](https://github.com/yt-dlp/yt-dlp/issues/10360), where
`--sub-format json3` raises `_UnsafeExtensionError`. Reading the issue: it fires
in yt-dlp's *filename* construction, which refuses to create `de.json3` on disk.
It has nothing to do with fetching. Any tool that reads the caption URL directly
is unaffected. Following this advice would silently triple every auto-caption
transcript.

**"json3 flags the scroll-duplicates so you can remove them."** This is what
*this project* believed, and it's wrong in an interesting way. Measured across
three videos:

| Video | events | `aAppend` | `aAppend` carrying a word |
|---|---|---|---|
| How to Speak (1h) | 2744 | 1371 | **0** |
| Never Gonna Give You Up | 104 | 51 | **0** |
| 3B1B neural networks | 1000 | 499 | **0** |

Every `aAppend` event holds exactly `"\n"`. Word count with the filter and
without it: **9086 either way.** There is nothing to remove — json3 never emits
the duplicate text in the first place. The dedup is a property of the format, not
a repair. (See ADR 0002's correction.)

## PO tokens

The live one. [yt-dlp #13075](https://github.com/yt-dlp/yt-dlp/issues/13075):
`/api/timedtext` began requiring a `pot=` parameter alongside `c=WEB` for some
requests. Fixed in yt-dlp by [#13234](https://github.com/yt-dlp/yt-dlp/pull/13234).

What matters is the **failure shape**: a request lacking the token gets **HTTP
200 with an empty body**, not an error status. Any code doing `json.loads(body)`
dies with `Expecting value: line 1 column 1` and tells the user nothing.

Checked on 2026-07-26: no `pot=` in the URLs for the videos tested, and fetches
succeed. So this is latent rather than active — which is precisely why it needs a
named error rather than a traceback.

## Rate limiting

No published limits. Consistent reports across sources:

- **~100–200 requests/hour per IP** before soft-blocking.
- **Datacenter ranges (AWS, GCP, Azure) are blocked outright**, often on the
  *first* request. A 429 on request one is an IP-reputation verdict, not a volume
  one.
- The documented fix is **rotating residential proxies**. Webshare's "Residential"
  tier specifically — not "Static Residential", not datacenter.
- **Cookie authentication is not a workaround.** youtube-transcript-api's README
  says its cookie support is currently broken, and separately that authenticating
  gets the *account* banned eventually. Don't.

Blocks surface as free text, not status codes — "Sign in to confirm you're not a
bot" is the common one.

## Transcript structuring, for the consumers

From the RAG literature, since the output is meant to be fed to something:

- Chunk on semantic boundaries at **300–600 tokens with ~50 token overlap**,
  preferring pauses, topic shifts and end-of-utterance over fixed windows.
- **Keep timestamps.** Every chunk should carry `video_id` + `start_sec` as a
  grounding primitive — it's what lets an answer say "jump to 12:34" and what
  lets an eval harness check retrieval against known timestamps.
- Segment at **pauses over a threshold and at every speaker change**.
- Normalise for embedding, keep an un-normalised copy for display.
- Real speaker labels require **audio diarization** (pyannote et al). Caption
  tracks carry `>>` — a boundary marker — and nothing else. There is no name in
  the data to recover.

Our 110-word default is ~145 tokens: a *reading* unit, deliberately below the
chunking range. `--words 250` gets closer to chunk-shaped. Paragraphs aren't
chunks and this isn't a chunker (ADR 0006).

## What this changed

| Finding | Change |
|---|---|
| 200 + empty body on missing PO token | Named error instead of a `JSONDecodeError` traceback |
| URLs are IP-bound and expire | 403 says so; `--proxy` applies to *both* requests, not just the fetch |
| Datacenter IPs blocked, ~hundreds/hour | `--proxy`, pacing between videos, exponential backoff with jitter |
| Blocks are free text, and terminal | `RateLimited` exception; batch runs stop and tell you to resume with `--skip-existing` |
| `_UnsafeExtensionError` is about disk | Recorded in ADR 0002 so nobody "fixes" json3 → vtt |
| `aAppend` never carries text | ADR 0002 correction; comments no longer overstate what the filter does |
| Offline tests can't see any of this | `tests/test_live.py`, run with `-m network` |

## Sources

- [yt-dlp #13075 — Some subtitles require POT now?](https://github.com/yt-dlp/yt-dlp/issues/13075)
- [yt-dlp #10360 — _UnsafeExtensionError with json3/srv3](https://github.com/yt-dlp/yt-dlp/issues/10360)
- [jdepoix/youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api) — IP blocking, proxies, broken cookie auth
- [Extract YouTube Transcripts Using InnerTube API](https://medium.com/@aqib-2/extract-youtube-transcripts-using-innertube-api-2025-javascript-guide-dc417b762f49) — the endpoint and client impersonation
- [How to Scrape Captions from YouTube](https://roundproxies.com/blog/scrape-youtube-captions/) — throttling, datacenter blocking
- [RAG for Video Transcripts](https://vidnavigator.com/en/blog/rag-for-video-transcripts) — chunking and timestamp guidance
