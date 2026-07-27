# 0012. Validate the model's answer against a domain model

- Status: Accepted
- Date: 2026-07-27

## Context

`--speakers` asks a language model who is talking and writes the answer into a
document. Between those two steps there was a JSON parse and a range check on
paragraph numbers, and nothing else. The model could name anyone, spell them
however it liked, and the document would record it as fact.

That is tolerable for a transcript somebody reads. It is not tolerable for the
thing this corpus is actually for — cross-referencing people across many
playlists — because the failure is silent and compounding. "Tom O'Neill", "Tom
O'Neil" and "O'Neill" are three separate individuals to any graph that joins on
name, and speech recognition guarantees all three will occur.

Frank Coyle's talk (transcribed with this tool, which is how it turned up) names
the shape of the fix: *Pydantic at the door, ontology at the ledger.* Validate
structure at the boundary, then validate **meaning** against a formal model of
the domain before anything is committed. His three motivating errors have exact
analogues here:

| His example | Here |
|---|---|
| an order status of "probably shipped" | a speaker the video's metadata never names |
| a payout to the support desk, not the buyer | the host and the guest turning out to be one person |
| a second refund on the same order | one person arriving under three spellings |

## Decision

A deterministic validator (`ontology.py`) sits between the model's answer and the
document. It is offline and testable, which is the point: **the check on a
probabilistic step must not itself be probabilistic.**

The domain model is small and comes from the video, not from us — the channel is
the host, the description and any credited title tail name the guest, spelled by
a human. Against that it enforces:

- **Membership.** A claimed speaker is resolved against the known people. A name
  that cannot be accounted for is kept but demoted to low confidence, never
  deleted: a guest introduced only in speech is real, just unverifiable.
- **Functionality — one canonical spelling per person.** Exact fold first, then a
  per-word near match for transcription slips, then a dropped middle name, then a
  bare surname. Every merge is reported.
- **Disjointness.** The host and the guest cannot be the same person.
- **Cardinality and coverage.** An implausible speaker count, or an attribution
  that attributes almost nothing, is flagged.

Violations go back to the model once, with the reason and the list of people the
metadata actually names — Coyle's loop closing. One retry, not a loop: his own
warning is that loops drift and cost money, and a second disagreement is a signal
to report rather than to keep asking. The retry is kept only if it breaks fewer
constraints than the original.

What survives is written into the document *including the flags*, so a reader
deciding whether to trust an attribution can see that the domain model objected.

## Consequences

- Cross-episode joining becomes possible, which was the point. A mangled name
  resolves to the description's spelling, so one person is one node.
- **Building it found three bugs, two of them in the validator itself**, which is
  the argument for having it in a form you can test:
  - A whole-string similarity ratio merged "Tim O'Neill" into "Tom O'Neill" —
    two people fused into one, the worst available error. Per-word comparison
    with a minimum length now refuses it.
  - Sharing a surname was enough to resolve a full name, which merged them
    again by a different route. Surname-only resolution now applies to a bare
    surname and nothing else.
  - Name extraction spanned sentence boundaries and newlines, yielding
    `"Sixties. O'Neill"` from a real description — which then became the
    canonical spelling for a real man.
- The name regex still over-collects organisations ("Spahn Ranch", "MK Ultra").
  That is the safe direction: a false positive costs a missed flag, a false
  negative would reject a real person.
- Everything here is heuristics over prose, and heuristics drift. The mitigation
  is that each rule is a named, separately-tested constraint rather than one
  opaque score, so a wrong call names itself.
- **This applies to `--speakers` and nothing else.** The rest of the tool is
  deterministic and has no probabilistic step to guard. Do not generalise the
  pattern to code that doesn't need it.
