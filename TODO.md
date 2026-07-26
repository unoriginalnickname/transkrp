# TODO

Known gaps, in rough priority order. Nothing here is half-finished work — the
tool is complete and tested as it stands; these are things it doesn't do yet.

Decisions that *have* been made live in [docs/adr/](docs/adr/README.md), and the
background research in
[docs/research/](docs/research/2026-07-youtube-transcript-extraction.md).

## Worth doing

- [ ] **Take the good ideas from `youtube-transcript-api`.** It solves a
  narrower problem but has had far more users find its edges. Specifically worth
  stealing: pluggable output formatters (it emits SRT and WebVTT; we only do
  markdown and JSON), its explicit `.translate()` API (we treat translation as a
  warning flag rather than something you can ask for), and its more granular
  exception types. Ideas, not code — it's MIT, so copying would be legal with
  attribution, but a hostile repackaging of someone's library is a bad way to
  exist.

- [ ] **Split speaker turns share a timestamp.** `_split_turns` splits a segment
  at `>>` but gives every resulting part the same start/end ms, so in a rapid
  exchange all the turns from one cue carry the same anchor. Interpolating across
  the cue by word count would be closer to the truth. Worth confirming it's
  visible in real output before touching it.

- [ ] **SRT / WebVTT output.** The one genuinely common request this can't serve.
  We already have the segments; it's a formatter, not a fetch.

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
