# TODO

## Worth doing

Nothing open.

---

Everything has been built, or checked and declined with the reason
recorded. Kept as a record so the declined items don't get re-derived.

Decisions that *have* been made live in [docs/adr/](docs/adr/README.md), and the
background research in
[docs/research/](docs/research/2026-07-youtube-transcript-extraction.md).

## Done

The three ideas worth taking from `youtube-transcript-api` have been taken —
subtitle formatters, granular exception types, and translation access (which
turned out to already exist via `--lang`). Ideas, not code: it's MIT, so copying
would be legal with attribution, but a hostile repackaging of someone's library
is a bad way to exist.

## Checked and closed without a change

- **An explicit `--translate LANG`** was on this list, borrowed from
  `youtube-transcript-api`'s `.translate()`. `--lang` already does it: YouTube
  lists all ~150 machine translations as ordinary auto tracks, so `--lang de`
  fetches the German one and flags it `translated: true`. A separate flag would
  be a worse alias — `--lang de` also prefers a *human* German track if one
  exists, which `--translate de` by definition could not.

- **Split speaker turns sharing a timestamp** was on this list as a minor
  accuracy bug. Measured on a 5,180-cue congressional hearing: 474 cues carry
  `>>` and **every one has it at the start**, so the mid-cue split never fires
  and no two parts ever share a timestamp. The branch stays as a cheap guard;
  there is nothing to fix. (Second time this session that defensive code turned
  out not to be load-bearing — the first was the `aAppend` filter, ADR 0002.)

## Not doing, and why

- **Publishing to PyPI.** There are hundreds of near-identical projects and the
  differentiation is a formatting layer, not a capability. The package builds
  and installs fine from the repo (`pip install .`, or
  `pip install git+https://github.com/unoriginalnickname/transkrp`), which is
  enough for the people who'd actually want it.

- **Parallel playlist fetches.** The obvious speedup, and directly
  counterproductive against a per-IP rate limiter — see
  [the research note](docs/research/2026-07-youtube-transcript-extraction.md#rate-limiting).

- **Naming speakers.** The caption tracks don't carry names; producing them means
  either guessing or diarizing the audio, which is a different tool.
  [ADR 0005](docs/adr/0005-turns-not-speakers.md).

- **Live tests in CI.** GitHub runners have datacenter IPs, which YouTube blocks
  outright, so the job would fail on a healthy tree.
  [ADR 0010](docs/adr/0010-ci-runs-offline-only.md).

## Done since this list was written

**Podcasts** are transcribed rather than refused: a show's name, an RSS feed or
an Apple Podcasts link resolves through the iTunes directory to the episode
audio, and `faster-whisper` produces the transcript when the feed publishes none
([ADR 0015](docs/adr/0015-transcribe-podcasts-rather-than-refusing-them.md)).
This had been done once by hand and lost; the committed code reproduces that
transcript exactly — 77 of 77 paragraphs identical in timestamp and text.

**Sponsor reads** are stripped by `--strip-sponsors`, opt-in, with the removed
spans declared in the frontmatter
([ADR 0014](docs/adr/0014-strip-sponsor-reads-on-request-and-say-so.md)). The
open question in the original entry — whether removing content silently is
acceptable in a document that claims to be a transcript — was answered "no", in
both directions: the flag is off by default, and when it does cut, the document
says where.

Earlier:

Non-English fallback, `--lang auto`, `--cookies` for age-restricted videos,
`--force`, `--version`, a single source of truth for the dependency pin, live
tests that skip rather than fail when a fixture video is pulled, and an MIT
licence.
