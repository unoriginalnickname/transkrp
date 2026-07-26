# 0009. Batch runs resume instead of restarting

- Status: Accepted
- Date: 2026-07-26

## Context

[0008](0008-playlists-and-multiple-urls.md) made a 200-video run possible.
Combined with the rate limits documented in
[the research note](../research/2026-07-youtube-transcript-extraction.md) —
a few hundred caption pulls an hour per IP — it also made a new failure certain:
the run gets blocked partway through.

The old behaviour made that as bad as it could be. A block at video 150 meant the
remaining 50 each failed in turn, hammering an endpoint that had just said stop,
and the rerun started again from video 1, refetching 150 transcripts already on
disk. The tool's response to being rate-limited was to send more requests.

Two problems, and they need each other: knowing what's already done, and knowing
when to stop.

The catch on the first is that the output filename is `<title-slug>-<id>.md`, and
the title is only knowable by probing — which is the request we're trying not to
spend. Checking after the probe would save the caption fetch and nothing else.

## Decision

**Skip on the id, read from the URL.** `video_id()` pulls the 11-character id out
of the URL with a regex, and `--skip-existing` looks for a file ending
`-<id>.<ext>`. The id is in the filename precisely so this works, and it costs no
requests at all. If the id can't be parsed, fetching is the safe answer.

`--skip-existing` covers all three output shapes: `-o DIR` looks in the
directory, `-o FILE` asks whether that file exists, no `-o` looks in the working
directory where the default name lands.

**A block ends the run.** `RateLimited`, a `LookupError` subclass, is raised by
`_get` on a persistent 429 and by `_extract` when yt-dlp's message matches a
block. A batch run catches it, stops, and prints how many were not fetched and
that `--skip-existing` will resume. Ordinary failures — private, unavailable, no
captions — still only skip that video, because they say nothing about the next
one.

`RateLimited` is a subclass rather than a new type so the "catch one exception"
contract in [0007](0007-failure-handling.md) still holds — that record explicitly
anticipated revisiting this "if callers ever need to distinguish 'unavailable'
from 'rate-limited' programmatically". They now do.

**Pace the fetches, not the skips.** The 1-second delay applies between actual
requests. A resume that skips 150 files shouldn't take two and a half minutes
doing nothing.

## Consequences

- `transkrp <playlist> -o ./notes/ --skip-existing`, rerun until it completes, is
  a working answer to a playlist larger than the rate limit.
- A second run over an unchanged directory costs zero requests and finishes in
  about a third of a second.
- **Skipping is by id, so it can't tell a stale file from a fresh one.** If a
  video's captions are corrected, `--skip-existing` will keep the old transcript.
  That is the right default for resuming and the wrong one for refreshing; the
  fix is to delete the file, and the flag is opt-in so nobody gets this by
  accident.
- Detecting a block means pattern-matching yt-dlp's free-text errors, which will
  drift. It only decides whether to give up early, so a miss costs a few wasted
  requests rather than a wrong result.
- Fixed in passing: `-o notes/` with a *single* video used to write a file called
  `notes`, because arity alone decided whether `-o` meant a directory. A trailing
  slash now means a directory whatever the arity — otherwise the first run of a
  growing collection poisons the directory the second run needs.
