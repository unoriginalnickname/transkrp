# transkrp

Fetches a YouTube transcript as a readable markdown file — prose with a
`[timestamp]` anchor on every paragraph, so any line traces back to the video.

```
pip install .
transkrp "https://www.youtube.com/watch?v=VIDEO_ID"
```

Or without installing — it's one file with one dependency:

```
pip install -r requirements.txt
python transkrp.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

Writes `<title-slug>-<video_id>.md`. Needs Python 3.10+.

```
-o PATH          output file, or a directory (several videos, or a trailing /)
-o -             stdout
-f, --format     md (default), json, srt, vtt
--json           shorthand for --format json
--list           show the caption tracks and exit
--lang KEY       force a track (e.g. en-orig), or 'auto' for the spoken language
--words N        target paragraph length (default 110)
--proxy URL      route both requests through a proxy
--cookies WHAT   browser name or cookies.txt, for age-restricted videos
--skip-existing  don't refetch what's already in the output directory
--force          refetch anyway, when captions have been corrected
--version
```

Several videos at once, and playlists and channels expand:

```
python transkrp.py "https://www.youtube.com/playlist?list=..." -o ./notes/ --skip-existing
```

A video shared from inside a playlist (`watch?v=X&list=Y`) is treated as that one
video, not the playlist around it. One failure doesn't stop the run — the error
goes to stderr and the rest continue.

### Playlists bigger than the rate limit

YouTube allows a few hundred caption pulls an hour per IP, so a long playlist
will get blocked partway. When that happens the run **stops** rather than
hammering a limiter that just said no, and tells you what's left. Rerun the same
command later: `--skip-existing` matches on the video id already in each
filename, so it resumes without spending a request on anything it has.

As a library:

```python
from transkrp import transcript
t = transcript(url)          # JSON-safe dict; raises LookupError on failure
t["paragraphs"][0]["text"]
```

Every failure is a `LookupError`, so catching that one type is enough. Three
subclasses are there when the difference matters — each implies a different move:

| | Means | Do |
|---|---|---|
| `RateLimited` | refused for volume or IP reputation | wait, or use a proxy |
| `Unavailable` | private, deleted, region-locked, age-gated | skip it for good |
| `NoCaptions` | video is fine, no usable track | try another `lang` |

## Output

```
---
title: ...
source: auto
lang: en
punctuated: true
turns: 518
---

[13:03] >> Right.

[13:04] >> So if you think the apocalypse is lingering, there's all sorts of
things you're not going to do or bother investigating...
```

`>>` marks a speaker change — the standard caption convention. It says the
speaker *changed*, not who they are; the tracks don't carry names, so neither
does this.

## Subtitle files

`-f srt` and `-f vtt` emit the cleaned cues rather than paragraphs — a real
subtitle file you can load in a player.

Worth having even though yt-dlp hands you a `.vtt` directly, because that one is
the scrolling caption box serialised frame by frame. Measured on the same
auto-caption track: **26,402 words from yt-dlp's `.vtt`, 9,086 from ours — 2.9×**.
Same subtitles, without every phrase three times, and with the lookalike
typography normalised.

Overlapping cues are truncated at the next cue's start, and zero-length ones are
given a millisecond, so players don't flicker or leave a caption stuck on screen.

## Frontmatter

Tells you whether to trust the file:

- `source` — `manual` (human-written) or `auto` (speech recognition).
- `punctuated` — whether it has sentence punctuation. **Detected, not assumed:**
  "auto" doesn't mean "raw". YouTube's newer ASR emits punctuation and speaker
  markers; older videos' doesn't. Some manual tracks are unedited dumps with
  neither.
- `translated` — an `en` auto track on a non-English video is a machine
  translation of a machine transcription. Treat with suspicion.
- `turns` — number of speaker changes.

## Track selection

In order: a human transcript in English, a human transcript in the language
spoken, the original speech recognition, and only then a machine translation.

That last ordering matters more than it looks. YouTube lists ~150 machine
translations of its own ASR alongside the original, so a German video offers an
`en` auto track — and taking it gives you a machine translation of a machine
transcription while the human German transcript sits one line below. Two lossy
steps where one would do.

`--lang auto` skips the English preference entirely. `--lang KEY` forces a
specific track, and an explicitly requested translation is honoured and flagged.

Keys aren't always the bare code: there's `en-orig` (the original spoken track),
`en-US`/`en-GB`, and multi-track videos expose `en-<trackid>`. Where several
tracks in one language exist and none is plainly named, the pick is arbitrary —
use `--list` to see them.

## Why not just yt-dlp

yt-dlp does the fetching here, and handles more than you'd expect — including
preferring manual captions over auto ones by itself. But it gives you a subtitle
file, not a transcript: cues broken mid-sentence, HTML entities, a timestamp
every three seconds.

For auto-captions it's worse than cosmetic. The `.vtt` re-serialises the whole
two-line caption box every time it scrolls, so a naive strip produced **14,514
words against this tool's 4,884** on the same talk — every phrase three times,
invisible unless you count.

This reads the `json3` format instead, which represents that scroll as an append
of a newline rather than by repeating the text. The duplication is absent by
construction; there's no dedup heuristic here to get wrong, and no threshold to
tune. (Tools that do strip `.vtt` have to guess, and a speaker who genuinely
repeats themselves is indistinguishable from the artifact.)

So: yt-dlp fetches; this reads a format that doesn't duplicate, splits speaker
turns, and reflows cues into paragraphs.

`youtube-transcript-api` is a fine alternative — it handles the duplication too,
and produces byte-identical segment text (verified: 676 segments, 0 differences).
It's chosen against only because it returns no video metadata, so the title would
need a second fetch.

## Limits

- No captions, no output.
- Speaker changes are marked, but speakers aren't named — the tracks don't say.
  Naming them means diarizing the audio, which is a different tool.
- ASR mangles proper nouns. The timestamps are there so anything load-bearing can
  be checked against the audio.
- YouTube rate-limits caption pulls per IP (a few hundred an hour) and blocks
  datacenter ranges outright. Fine from a laptop; from a cloud box you'll need
  `--proxy` with a residential endpoint.
- Paragraphs are reading units, not RAG chunks. `--words 250` gets you closer to
  chunk-shaped; every paragraph carries `start_ms` and `turn` so a consumer can
  group them itself.

## Development

```
pip install -e ".[dev]"
python -m pytest -q          # 150 offline tests, no network
python -m pytest -m network  # 12 live tests, really hits YouTube
```

Offline tests come in two kinds. Most stub the caption fetch at `_get` and cover
the things that go wrong quietly: the paragraph break rules, punctuation
detection, the retry policy, batch resume, and the empty-body response that means
a PO token is needed.

The rest (`tests/test_http.py`) run against a real HTTP server on localhost, so
the rate-limit path is *observed* rather than assumed — a genuine 429 off a
socket, real `urllib`, real timeouts, and the batch run giving up and printing
how to resume. The alternative, provoking YouTube into really rate-limiting you,
costs a home IP that can't fetch transcripts for hours in exchange for one test
run.

The live ones are the ones that matter when something breaks — the offline suite
will happily report 128 green while the tool is completely broken against
YouTube, because everything it depends on is an undocumented endpoint that
changes without notice. They're not in CI on purpose: GitHub runners have
datacenter IPs and YouTube blocks those outright, so the job would fail on a
healthy tree ([ADR 0010](docs/adr/0010-ci-runs-offline-only.md)). Run them
locally when something looks wrong; they take five seconds.

Design decisions that aren't obvious from the code are in
[docs/adr/](docs/adr/README.md) — including why `json3` and not `vtt`, and why
speakers aren't named. The survey behind them is in
[docs/research/](docs/research/2026-07-youtube-transcript-extraction.md): how
extraction actually works, what was measured against the live API, and which
widely-repeated advice turned out to be wrong.
