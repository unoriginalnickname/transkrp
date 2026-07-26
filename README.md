# transkrp

Fetches a YouTube transcript as a readable markdown file — prose with a
`[timestamp]` anchor on every paragraph, so any line traces back to the video.

```
pip install -r requirements.txt
python transkrp.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

Writes `<title-slug>-<video_id>.md`.

```
-o PATH        output file, or a directory when there are several videos
-o -           stdout
--json         JSON instead of markdown
--list         show the caption tracks and exit
--lang KEY     force a track (e.g. en-orig)
--words N      target paragraph length (default 110)
--proxy URL    route both requests through a proxy
```

Several videos at once, and playlists and channels expand:

```
python transkrp.py "https://www.youtube.com/playlist?list=..." -o ./notes/
```

A video shared from inside a playlist (`watch?v=X&list=Y`) is treated as that one
video, not the playlist around it. One failure doesn't stop the run — the error
goes to stderr and the rest continue.

As a library:

```python
from transkrp import transcript
t = transcript(url)          # JSON-safe dict; raises LookupError on failure
t["paragraphs"][0]["text"]
```

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

Manual wins over auto. Keys aren't always `en`: there's `en-orig` (the original
spoken track), `en-US`/`en-GB`, and multi-track videos expose `en-<trackid>`.
Where several English tracks exist and none is plainly `en`, the pick is
arbitrary — use `--list` to see them, `--lang` to choose.

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
python -m pytest tests/ -q
```

93 tests, no network — the caption fetch is stubbed at `_get`. They cover the
things that go wrong quietly: scroll-duplicate removal, the paragraph break
rules, punctuation detection, the retry policy, and the empty-body response that
means a PO token is needed.

Design decisions that aren't obvious from the code are in
[docs/adr/](docs/adr/README.md) — including why `json3` and not `vtt`, and why
speakers aren't named.
