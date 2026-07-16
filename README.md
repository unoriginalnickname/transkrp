# transkrp

Fetches a YouTube transcript as a readable markdown file — prose with a
`[timestamp]` anchor on every paragraph, so any line traces back to the video.

```
pip install -r requirements.txt
python transkrp.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

Writes `<title-slug>-<video_id>.md`. Options: `-o PATH` (or `-o -` for stdout),
`--json`, `--list` to see the caption tracks, `--lang KEY` to force one.

As a library:

```python
from transkrp import transcript
t = transcript(url)          # JSON-safe dict
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

For auto-captions it's worse than cosmetic. The `.vtt` repeats each line as the
two-line display scrolls, so a naive strip produced **14,514 words against this
tool's 4,884** on the same talk — every phrase three times, invisible unless you
count. This reads the `json3` format instead, where the scroll-repeat events are
explicitly flagged.

So: yt-dlp fetches; this drops the scroll-duplicates, splits speaker turns, and
reflows cues into paragraphs.

`youtube-transcript-api` is a fine alternative — it handles the duplication too,
and produces byte-identical segment text (verified: 676 segments, 0 differences).
It's chosen against only because it returns no video metadata, so the title would
need a second fetch.

## Limits

- No captions, no output.
- Speaker changes are marked, but speakers aren't named — the tracks don't say.
- ASR mangles proper nouns. The timestamps are there so anything load-bearing can
  be checked against the audio.
- YouTube rate-limits datacenter IPs. Fine from a laptop, flaky from a cloud box.
