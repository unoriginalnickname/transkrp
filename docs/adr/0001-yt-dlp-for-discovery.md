# 0001. yt-dlp finds the caption tracks

- Status: Accepted
- Date: 2026-07-16 (recorded 2026-07-26)

## Context

Something has to turn a video URL into a caption track URL. Four options:

1. **YouTube Data API v3.** Official, OAuth'd, quota'd. It lists caption tracks
   but `captions.download` only works for videos you own. It cannot give us the
   text of someone else's video, which is the entire task.
2. **InnerTube directly** — POST to `/youtubei/v1/player` impersonating the
   Android client, scrape `INNERTUBE_API_KEY` out of the watch page, read
   `captions.playerCaptionsTracklistRenderer.captionTracks`. This is what most
   blog posts and commercial scrapers do.
3. **youtube-transcript-api**, which wraps the same endpoint.
4. **yt-dlp**, which also wraps it, plus about a decade of accumulated repairs.

Option 2 is a moving target. The client versions, the key extraction, the
signature scheme and the bot checks all change without notice; keeping up is a
full-time job that several projects already do.

## Decision

yt-dlp does discovery and metadata. We call `extract_info` and read
`subtitles` / `automatic_captions` off the result.

youtube-transcript-api is a fine alternative and produces byte-identical segment
text (verified: 676 segments, 0 differences on a test video). It is chosen
against only because it returns no video metadata, so the title — which the
output filename and frontmatter both need — would cost a second fetch.

## Consequences

- We inherit yt-dlp's handling of things we did not want to think about: age
  gates, signature ciphers, client impersonation, PO tokens, `--proxy`,
  preferring manual captions over auto ones.
- We inherit its release cadence too. When YouTube changes something, the fix
  arrives as a yt-dlp upgrade rather than as work here. `requirements.txt` floors
  the version rather than pinning it, deliberately.
- `extract_info` costs a full format extraction we don't use. It is about a
  second per video, which is cheaper than being wrong about PO tokens.
- If yt-dlp is ever abandoned, this is the decision to revisit — and the blast
  radius is `_extract`, `probe` and `expand`.
