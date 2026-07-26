# TODO

Known gaps, in rough priority order. Nothing here is half-finished work — the
tool is complete and tested as it stands; these are things it doesn't do yet, or
decisions nobody has made.

Decisions that *have* been made live in [docs/adr/](docs/adr/README.md), and the
background research in
[docs/research/](docs/research/2026-07-youtube-transcript-extraction.md).

## Functional gaps

- [ ] **Fall back to the video's own language when there's no English track.**
  `pick_track` only looks for English, so a German-only video errors with "no
  English captions - pass `--lang`". That's accurate but means running `--list`
  first to discover the key. Falling back to `info["language"]`, or accepting
  `--lang auto`, would make `transkrp <non-english-url>` just work. *Most likely
  thing to annoy a real user.*

- [ ] **Expose cookies for age-restricted and sign-in-required videos.** They
  currently just fail. yt-dlp handles them via `cookiesfrombrowser` /
  `cookiefile`; pass `--cookies-from-browser` through to the extractor. Note the
  caption fetch is our own `urllib` (ADR 0003), so cookies may need plumbing
  there too — the same trap `--proxy` fell into.

## Decisions for the author

- [ ] **Choose a licence.** There is no `LICENSE` file, and `pyproject.toml`
  deliberately omits the `license` field — inventing one would be a claim about
  intent rather than a packaging detail. Without it nobody else can legally use
  this, and it blocks publishing to PyPI. Needs a decision, then a `LICENSE` file
  and the `pyproject` field.

- [ ] **Publish to PyPI?** Currently `pip install .` from a checkout only.
  Blocked on the licence above. May not be wanted at all.

## Housekeeping

- [ ] **Stop declaring `yt-dlp>=2026.7.4` in two places.** It's in both
  `requirements.txt` and `pyproject.toml`, which can drift. Make
  `requirements.txt` just `-e .`, or delete it and point the README at
  `pip install .`.

- [ ] **`--force`, so corrected captions can be refetched.** `--skip-existing`
  matches on video id alone and cannot tell a stale transcript from a fresh one.
  If YouTube corrects a track, the old file is kept forever and the only fix is
  deleting it by hand. Known consequence, recorded in
  [ADR 0009](docs/adr/0009-batch-runs-resume.md).

- [ ] **Make the live tests degrade gracefully if their fixture videos vanish.**
  `tests/test_live.py` pins `jNQXAC9IVRw` and `Unzc731iCUY`. If MIT ever pulls
  "How to Speak", the suite fails with assertion errors that read like a code
  regression rather than a missing video. Detect "unavailable" and skip naming
  the video, the way rate limits already skip.

- [ ] **`--version`.** Conventional for a packaged CLI, trivial via
  `importlib.metadata`.

- [ ] **Split speaker turns share a timestamp.** `_split_turns` splits a segment
  at `>>` but gives every resulting part the same start/end ms, so in a rapid
  exchange all the turns from one cue carry the same anchor. Interpolating across
  the cue by word count would be closer to the truth. Low priority — worth
  confirming it's visible in real output before touching it.

## Deliberately not doing

- **Parallel playlist fetches.** The obvious speedup, and directly
  counterproductive against a per-IP rate limiter — see
  [the research note](docs/research/2026-07-youtube-transcript-extraction.md#rate-limiting).

- **Naming speakers.** The caption tracks don't carry names; producing them means
  either guessing or diarizing the audio, which is a different tool.
  [ADR 0005](docs/adr/0005-turns-not-speakers.md).

- **Live tests in CI.** GitHub runners have datacenter IPs, which YouTube blocks
  outright, so the job would fail on a healthy tree.
  [ADR 0010](docs/adr/0010-ci-runs-offline-only.md).
