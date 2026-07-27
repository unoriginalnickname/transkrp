# Architecture decision records

Short records of the decisions that aren't obvious from reading `transkrp.py`,
and that someone would otherwise be tempted to reverse.

Most of this tool is a thin wrapper around yt-dlp. The parts that aren't thin are
the parts where the obvious approach is quietly wrong — a `.vtt` strip that
triples the word count, a punctuation assumption that holds for most videos, a
retry loop that makes a rate-limit worse. Those are what's recorded here.

Format is [Nygard-style](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):
context, decision, consequences. One file per decision, numbered, never edited
once accepted — superseded by a later record instead.

| # | Decision | Status |
|---|----------|--------|
| [0001](0001-yt-dlp-for-discovery.md) | yt-dlp finds the caption tracks | Accepted |
| [0002](0002-json3-not-vtt.md) | Read json3, not vtt | Accepted |
| [0003](0003-fetch-caption-urls-ourselves.md) | Fetch the caption URL directly | Accepted |
| [0004](0004-detect-punctuation.md) | Detect punctuation, never infer it | Accepted |
| [0005](0005-turns-not-speakers.md) | Mark turns, don't name speakers | Accepted |
| [0006](0006-paragraph-segmentation.md) | Break on turn, then silence, then length | Accepted |
| [0007](0007-failure-handling.md) | Every failure is a LookupError with a next step | Accepted |
| [0008](0008-playlists-and-multiple-urls.md) | Playlists expand; several videos need a directory | Accepted |
| [0009](0009-batch-runs-resume.md) | Batch runs resume instead of restarting | Accepted |
| [0010](0010-ci-runs-offline-only.md) | CI runs the offline suite only | Accepted |
| [0011](0011-exhaust-the-metadata-before-generating.md) | Exhaust the metadata before generating anything | Accepted |
| [0012](0012-validate-the-model-against-a-domain-model.md) | Validate the model's answer against a domain model | Accepted |

The survey these draw on is
[docs/research/2026-07-youtube-transcript-extraction.md](../research/2026-07-youtube-transcript-extraction.md)
— how extraction actually works, what was measured, and which published advice
turned out to be wrong.

## Writing a new one

Copy the shape of an existing record. Worth writing when a choice was made
against a plausible alternative and the reason isn't visible in the code — not
for every function. If the answer to "why not the obvious thing?" is in a commit
message, it belongs here instead.
