# transkrp

Fetches a YouTube transcript as a readable markdown file — prose with a
`[timestamp]` anchor on every paragraph, so any line traces back to the video.

## Tech stack

| | | |
|---|---|---|
| **Python 3.10+** | required | Both dependencies floor there |
| **yt-dlp** | required | The only runtime dependency: discovery, metadata, chapters, track list |
| **stdlib `urllib`** | required | The caption fetch itself, and SponsorBlock |
| **SponsorBlock API** | optional | Ad timings for `--strip-sponsors`; stdlib only, ships with the core |
| **`claude` CLI** | optional | `--speakers` and the corpus graph. No API key |
| **nameparser + rapidfuzz** | optional | Name canonicalisation. `pip install ".[speakers]"` |
| **faster-whisper** | podcasts | Actual speech recognition, for audio nobody published a transcript for. `pip install ".[podcast]"` |
| **iTunes Search API** | podcasts | Resolves a show's name to its RSS feed. No key, stdlib only |
| **pytest** | dev | 458 offline tests, 21 live ones behind a marker |

**For YouTube, transcription is YouTube's** — this reads the caption track it
already published, and the work is downstream of ASR: de-duplication,
paragraphing, attribution. No audio is decoded on that path.

**For podcasts it's ours**, because most publish no transcript. That's the only
place here that decodes audio, and the only heavy dependency.

Captions are read as `json3`, not `.vtt`; that choice is why the output isn't
triplicated ([ADR 0002](docs/adr/0002-json3-not-vtt.md)). Optional pieces import
lazily, so an install without the extras still fetches transcripts.

## Use

```
pip install .
transkrp "https://www.youtube.com/watch?v=VIDEO_ID"
```

Writes `<title-slug>-<video_id>.md`.

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
--strip-sponsors drop sponsor reads (SponsorBlock timings); cut spans are recorded
--speakers       name the speakers and attribute each paragraph (needs `claude`)
--model ID       model for --speakers (default: whatever claude uses)
--playlist       take the whole playlist / every episode, not just the one
--episode TITLE  which podcast episode (default: the most recent)
--whisper-model  model for podcasts, which have no captions to fetch (default small)
--force          refetch anyway, when captions have been corrected
--version
```

Several videos at once, and playlists and channels expand:

```
transkrp "https://www.youtube.com/playlist?list=..." -o ./notes/ --skip-existing
```

A video shared from inside a playlist (`watch?v=X&list=Y`) is treated as that one
video; `--playlist` says you meant the playlist. One failure doesn't stop a run.

YouTube allows a few hundred caption pulls an hour per IP, so a long playlist
gets blocked partway. The run then **stops** rather than hammering a limiter that
just said no. Rerun the same command later — `--skip-existing` matches on the
video id in each filename, so it resumes for free.

As a library:

```python
from transkrp import transcript
t = transcript(url)          # JSON-safe dict; raises LookupError on failure
```

Every failure is a `LookupError`. Three subclasses when the difference matters:
`RateLimited` (wait, or use a proxy), `Unavailable` (skip it for good),
`NoCaptions` (try another `lang`).

## Output

```
---
title: The Man Involved In Every American Conspiracy
channel: Jesse Michels
published: 2024-08-22
url: https://www.youtube.com/watch?v=2SQXAPCdmPE
source: manual
lang: en
punctuated: false
note: unpunctuated speech recognition - no sentence breaks or speaker labels
---

# The Man Involved In Every American Conspiracy

## Government UFO Disclosure

[04:12](https://www.youtube.com/watch?v=2SQXAPCdmPE&t=252s) so the thing about
the Blue Book files is that...
```

Headings are the creator's own chapter titles, from the video's metadata — a
33,000-word interview arrives as 19 navigable sections rather than one wall.
Neither headings nor timestamp links are generated; both were in the data already
being fetched ([ADR 0011](docs/adr/0011-exhaust-the-metadata-before-generating.md)).

`>>` marks a speaker change, the standard caption convention — but **expect it to
be absent.** On a 30-episode interview playlist: `turns: 1` on all 30, two people
talking, no marker anywhere. A congressional hearing carried 474.

The frontmatter tells you whether to trust the file: `source` (`manual` or
`auto`), `punctuated` (**detected, not assumed** — 18 of those 30 were
unpunctuated, *including manual tracks*, which are often an ASR export somebody
uploaded), `translated` (an `en` auto track on a non-English video is a machine
translation of a machine transcription), and `turns`.

## Podcasts

Same command. A show's name, an RSS feed, or an Apple Podcasts link all work:

```
transkrp "The Valued Cultures Podcast"                    # the latest episode
transkrp "The Valued Cultures Podcast" --list             # what's in the feed
transkrp "The Valued Cultures Podcast" --episode "Garrett Young"
transkrp "https://feed.podbean.com/valuedcultures/feed.xml" --playlist -o ./notes/
```

Needs `pip install ".[podcast]"` — this is the one path that decodes audio, since
most podcasts publish no transcript. If the feed *does* publish one
(Podcasting 2.0's `<podcast:transcript>`), it's used instead and the run takes
seconds rather than minutes.

The route is: **iTunes Search API** → the show's real RSS feed → the episode's
`<enclosure>` MP3 → **faster-whisper** (`small`, int8, CPU) → the same paragraphs
every other document here has. About 4.5 minutes of CPU for a 41-minute episode.

**Amazon Music and Spotify links don't work, and can't.** yt-dlp refuses them for
DRM, and the pages are JavaScript shells — Amazon's serves 11KB with no title
element at all — so there's nothing to scrape even before the DRM. Pass the
show's name instead; that's what the error says too.

A show means its **most recent episode**, not all of them — the inverse of the
playlist rule, because every episode is a whisper run and a 21-episode feed is
about fourteen hours of CPU. `--playlist` takes them all.

The output declares what it is:

```yaml
source: whisper  # generated by speech recognition, not a published transcript
model: faster-whisper small (int8, CPU)
audio: https://mcdn.podbean.com/mf/web/hapjw46hvmxsa8z9/VC_GarrettYoung_031026.mp3
note: no transcript is published for this episode; every word here is ASR output
  and names in particular should be checked against the audio
```

That note is the important line. Whisper punctuates, so the output *looks* clean
in a way an unpunctuated auto-caption track doesn't — but `small` handles
conversational English well and proper nouns badly, which is exactly what a
corpus of people is built from. Timestamps link into the MP3 with `#t=`, so any
name can be checked against the audio in one click. [ADR 0015](docs/adr/0015-transcribe-podcasts-rather-than-refusing-them.md).

## Sponsor reads (`--strip-sponsors`)

Thirty near-identical sponsor reads across a series is worse than mild noise in a
corpus you search: repetition is what makes a passage look salient, so the one
thing the episodes are *not* about gets boosted for being frequent.

`--strip-sponsors` drops them using [SponsorBlock](https://sponsor.ajay.app/)'s
crowd-sourced timings, and **records what it removed**:

```yaml
sponsors_removed: 33:41-35:24  # cut from the transcript, timings from SponsorBlock
```

Off by default — a document called a transcript should contain what was said
unless you asked otherwise, and the crowd's timings are only as good as the
crowd. On the first real video tried, the segment began two sentences early and
took part of the conclusion with it. The frontmatter is how you find out.
[ADR 0014](docs/adr/0014-strip-sponsor-reads-on-request-and-say-so.md).

Nothing here can fail a fetch: no submissions, a timeout, or the service being
down all mean "no segments". The video ID is never sent — the query is keyed by
the first four characters of its hash.

## Who said what (`--speakers`)

Names the speakers and attributes each paragraph, by running the
[`claude`](https://claude.com/claude-code) CLI. **No API key.** Roughly ten
seconds per short episode. Needs `pip install ".[speakers]"`.

```
[00:01] **Jesse Michels**: on august 9 1969 charles manson led a group of...
[04:36] **Tom O'Neill?**: even lived with dennis wilson of the beach boys...
```

The names come from the video's metadata, not the transcript — the channel is the
host, the description names the guest *spelled correctly*. That's the whole
trick: ASR mangles exactly the proper nouns a cross-referencing corpus is built
from. The description says **Tom O'Neill**; the transcript says "tom o'neil".

**`?` means the model was inferring**, and most labels in a narrated video carry
one — clips and voiceover make turn-taking genuinely ambiguous. A paragraph it
won't commit to is left unattributed. Treat a `?` as a lead; the timestamp links
to the second of video that settles it.

## A graph of a corpus (`build_graph.py`)

A transcript answers "what was said". A directory of them can answer who keeps
appearing alongside whom, and on what basis.

```
python build_graph.py corpus/ai-engineer      # writes corpus/ai-engineer/graph.json
```

Reads the markdown already in that directory — no refetching — and extracts
people and relationships from a closed set of six kinds (`interviewed`,
`worked_with`, `cites`, `co_appeared`, `discussed`, `opposed`). Closed because an
open set fills with near-synonyms that fragment the graph.

**Every edge cites its evidence, and the citation is checked.** If the quote
doesn't appear verbatim in the transcript, the edge is *discarded* — a citation
that doesn't resolve is worse than none, because it looks like proof. Rejections
are counted, so a model inventing quotes shows up as a number rather than a
quietly smaller graph
([ADR 0012](docs/adr/0012-validate-the-model-against-a-domain-model.md)).

People resolve through `ontology.py`, so one person is one node however the
transcripts spell them. Runs are resumable, and videos that failed are listed in
the output rather than absorbed into it
([ADR 0013](docs/adr/0013-a-degraded-graph-must-not-pass-for-a-finished-one.md)).

## Reading it in Obsidian

```
python obsidian.py corpus/ai-engineer     # then open that folder as a vault
```

Obsidian is the viewer — there isn't one here, and there shouldn't be. But it
draws a line between two notes only when one links to the other, and transcripts
contain no `[[links]]` at all, so a corpus opens as forty disconnected dots with
every relationship sitting invisible in `graph.json`.

This writes the missing links: a note per person, linking to the transcripts they
appear in and the people they connect to, **each connection carrying the quote
that evidences it and a timestamp into the video**.

```markdown
## Connections

- was publicly disagreed with by [[Ian Livingstone]]
  > So, I think first and foremost, I'm coming for you Dex.
  — [10:28](https://www.youtube.com/watch?v=c35YoMdnI78&t=628s) in The Great Loops Debate
```

**The transcripts are never modified.** Every link lives in a new note under
`People/`, because Obsidian draws the same line whichever end holds it — and the
transcripts are the verbatim record, possibly annotated by hand. Delete the
folder and the corpus is byte-for-byte what it was.

### Link strength

Not every connection is worth the same, so they don't render the same. Obsidian
has **no per-link weight** — the thickness slider is global — so the hierarchy is
encoded in the two things it does read: node resolution, and tags.

| | What it is | How it looks |
|---|---|---|
| **Stated** | the speaker said it | `## Connections`, a normal link |
| **Implied** | the model inferred it | `## Possible connections`, hedged at the heading |
| **Local** | known only by a first name | a scoped link — small, dim, unresolved |

That last tier is why a first name doesn't just get dropped. A bare `[[Barry]]`
would merge every Barry in the corpus into one hub joining videos that share
nothing, so the link carries the video id — `[[Barry (c35YoMdnI78)|Barry]]` —
which keeps them apart while still drawing the faint node Obsidian gives an
unresolved link for free. On this corpus that scoping separates **29 people where
unscoped would have merged them into 26**.

To see the tiers, add two colour groups in Graph view → Groups:
`tag:#person/recurring` and `tag:#person/named`.

`--weights` additionally emits `[[Name]]::3` / `::1` for the
[weighted-graph plugin](https://github.com/jamesms36/obsidian-weighted-graph),
the only way to get a genuinely thicker line. Off by default, because stock
Obsidian shows that suffix as literal text.

Measured: 152 resolved links, 34 scoped weak ones, **0 dangling**.
[ADR 0016](docs/adr/0016-obsidian-is-the-graph-viewer.md),
[ADR 0017](docs/adr/0017-three-tiers-of-link-strength.md).

## Subtitle files

`-f srt` and `-f vtt` emit the cleaned cues rather than paragraphs. Worth having
even though yt-dlp hands you a `.vtt`, because that one is the scrolling caption
box serialised frame by frame: **26,402 words from yt-dlp's `.vtt`, 9,086 from
ours — 2.9×**. Overlapping cues are truncated at the next cue's start and
zero-length ones given a millisecond, so players don't flicker.

## Track selection

In order: a human transcript in English, a human transcript in the language
spoken, the original speech recognition, and only then a machine translation.

That last ordering matters. YouTube lists ~150 machine translations of its own
ASR alongside the original, so a German video offers an `en` auto track — taking
it gives you a machine translation of a machine transcription while the human
German transcript sits one line below.

`--lang auto` skips the English preference. `--lang KEY` forces a track and an
explicitly requested translation is honoured and flagged. Keys aren't always the
bare code — `en-orig`, `en-US`, `en-<trackid>` — so use `--list`.

## Why not just yt-dlp

yt-dlp does the fetching here. But it gives you a subtitle file, not a
transcript: cues broken mid-sentence, HTML entities, a timestamp every three
seconds. For auto-captions it's worse than cosmetic — the `.vtt` re-serialises
the whole caption box every time it scrolls, so a naive strip produced **14,514
words against this tool's 4,884** on the same talk.

This reads `json3`, which represents that scroll as an append rather than by
repeating text. The duplication is absent by construction: no dedup heuristic to
get wrong, no threshold to tune. (Tools that strip `.vtt` have to guess, and a
speaker who genuinely repeats themselves is indistinguishable from the artifact.)

`youtube-transcript-api` is a fine alternative — byte-identical segment text,
verified. It's chosen against only because it returns no video metadata.

## Limits

- No captions, no output — on YouTube. A podcast with no transcript gets one
  made, which is slower and less accurate; a YouTube video with captions
  disabled is simply not available.
- The tracks carry no names, so the default output has none. `--speakers` infers
  them from metadata — a guess with a `?` on it, not diarization.
- ASR mangles proper nouns. The timestamps are there so anything load-bearing can
  be checked against the audio.
- YouTube rate-limits caption pulls per IP and blocks datacenter ranges outright.
  Fine from a laptop; from a cloud box you'll need `--proxy` with a residential
  endpoint.
- Paragraphs are reading units, not RAG chunks. `--words 250` gets closer; every
  paragraph carries `start_ms` and `turn` so a consumer can group them itself.

## Development

```
pip install -e ".[dev]"
python -m pytest -q          # 458 offline tests, no network
python -m pytest -m network  # 21 live tests, really hits YouTube
```

Most offline tests stub the caption fetch at `_get`, or the `claude` CLI, or
whisper, and cover what goes wrong quietly: paragraph breaks, punctuation
detection, the retry policy, batch resume, name canonicalisation, feed parsing,
the evidence check that discards an unsupported edge, and the empty-body response
that means a PO token is needed.
`tests/test_http.py` runs against a real localhost server, so the rate-limit path
is *observed* — a genuine 429 off a socket, real `urllib`, real timeouts.

The live tests are the ones that matter when something breaks: the offline suite
will happily report green while the tool is broken against YouTube, because
everything it depends on is an undocumented endpoint that changes without notice.
They're out of CI on purpose — GitHub runners have datacenter IPs, which YouTube
blocks, so the job would fail on a healthy tree
([ADR 0010](docs/adr/0010-ci-runs-offline-only.md)).

Design decisions live in [docs/adr/](docs/adr/README.md); the survey behind them
is in [docs/research/](docs/research/2026-07-youtube-transcript-extraction.md).
