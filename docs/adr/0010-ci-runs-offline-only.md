# 0010. CI runs the offline suite only

- Status: Accepted
- Date: 2026-07-26

## Context

[The live tests](../../tests/test_live.py) exist because the offline suite stubs
the network and would therefore report every test passing while the tool is
completely broken against YouTube. Having written them, the obvious next move is
to run them in CI — nightly, so format changes get caught within a day.

That doesn't work, for a reason the research turned up:
[YouTube blocks datacenter IP ranges outright](../research/2026-07-youtube-transcript-extraction.md#rate-limiting).
AWS, GCP and Azure, often on the *first* request — it is a judgement about IP
reputation, not about volume. GitHub's hosted runners are Azure. A live job there
would fail on a perfectly healthy tree.

The options were:

1. **Run them anyway.** A job that fails most days is a job everyone learns to
   ignore, and it takes the rest of the pipeline's credibility with it.
2. **Run them through a residential proxy.** Technically works. Means a paid
   proxy subscription and a repository secret, for a project that is one file.
3. **Allow the job to fail** (`continue-on-error`). A check that cannot fail is
   not a check; it is a decoration that costs six minutes a day.
4. **Leave them out of CI.**

## Decision

CI runs `python -m pytest -q` — the offline suite, since `-m "not network"` is
the configured default — on Ubuntu and Windows, on the oldest and newest
supported Python. Plus `transkrp --help`, which catches a packaging mistake that
leaves the installed command broken while every import-based test still passes.

The live tests stay a deliberate local command: `python -m pytest -m network`.
They are documented in the README and in the test module's own docstring.

Windows is in the matrix for a specific reason rather than for symmetry: every
platform bug this tool has actually had was Windows-only — cp1252 on a redirected
stdout, and CRLF translation making `-o -` and `-o file` disagree about the same
document.

## Consequences

- CI is fast, deterministic, and green means something.
- **Nothing automatically detects YouTube changing the format.** That is the real
  cost, and it is accepted knowingly rather than papered over with a job that
  cries wolf. The mitigation is that the live suite is one command, takes five
  seconds, and is the first thing to run when something looks wrong.
- If this ever justifies a proxy subscription, a scheduled workflow with a
  `PROXY_URL` secret and `--proxy` is the shape of the answer — the flag already
  routes both the extraction and the caption fetch.
- A contributor without network access can still run everything CI runs.
