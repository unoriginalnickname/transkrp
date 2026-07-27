# TODO

## Worth doing

- [ ] **Strip sponsor reads.** Found by dogfooding a 30-episode interview
  playlist: every transcript opens with "huge thank you to our sponsor…
  discount code…". For a searchable knowledge base that is pure noise, and it
  is noise that recurs identically across every episode of a series, which is
  exactly the shape that poisons retrieval. SponsorBlock has crowd-sourced
  segment timings and yt-dlp already speaks to it (`--sponsorblock-mark`); the
  work here is dropping cues whose timings fall in a sponsor segment, plus a
  flag to control it. Would need its own ADR: a second network dependency, and
  a decision about whether removing content silently is acceptable in a
  document that claims to be a transcript.

---

Everything else has been built, or checked and declined with the reason
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

Non-English fallback, `--lang auto`, `--cookies` for age-restricted videos,
`--force`, `--version`, a single source of truth for the dependency pin, live
tests that skip rather than fail when a fixture video is pulled, and an MIT
licence.
