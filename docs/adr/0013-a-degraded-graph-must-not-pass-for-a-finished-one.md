# 0013. A degraded graph must not pass for a finished one

- Status: Accepted
- Date: 2026-07-28

## Context

[ADR 0012](0012-validate-the-model-against-a-domain-model.md) put a deterministic
validator between the model and the document. `graph.py` inherits that discipline
and adds its own: every edge cites a verbatim quote, and an edge whose quote does
not occur in the transcript is discarded rather than demoted.

Running it over a real 40-video corpus showed that the check was sound and the
*reporting around it* was not. Three separate defects, all with the same shape:
the graph came out looking better than it was, and nothing in the output said so.
A wrong graph that announces its wrongness is a bug. A wrong graph that reads as
finished is a worse bug, because it is the one that gets used.

**Provenance is not relevance.** `quote_is_real` proves a citation exists in the
transcript. It says nothing about whether the citation is *about* the two people
it is attached to. The corpus produced an `interviewed` edge between two people
whose evidence was a true sentence about a third person entirely. It passed the
strongest check in the module and was nonsense — and it looked *more* credible
than an uncited claim would have.

**A non-person became the graph's centre.** The uploading channel was extracted
as a person. On a conference channel that is an organisation, and it became the
biggest hub in the graph: 14 of 107 edges hung off "AI Engineer", including the
assertion that it had interviewed its own speakers. A hub is exactly the node a
reader trusts most.

**A failure was indistinguishable from a finding.** `extract` caught the CLI's
`LookupError` and returned an empty result — the same value a talk with genuinely
nobody in it produces. On one run the CLI failed transiently for ten consecutive
videos and the output reported ten talks containing no people. The graph was a
quarter of the size it should have been and said nothing about why.

## Decision

Three additions, each aimed at the gap between what the graph knew and what it
showed.

**`quote_supports` requires at least one endpoint to be named in the quote.**
Fuzzily, via the same near-match `ontology` uses, because ASR writes "Dex Horty"
for "Dex Horthy" and that is still a mention.

Deliberately weak. Requiring *both* names is the obvious stronger rule and it is
wrong: the other party in a real citation is usually a pronoun — "Dex covered
this well in his talk" — so the strict version rejects sound edges to catch a
rarer bad one. Weak but real: it catches a quote that is about somebody else.

Rejections are counted as `irrelevant`, separately from `rejected`. A model
inventing a citation and a model misfiling a genuine one are different failures
with different fixes, and summing them hides which one is happening.

**The channel is not a person.** Distinguishing an interview host (a person, who
belongs in the graph) from a conference channel (an organisation, which does not)
is not reliably automatable from the metadata. So the graph declines to guess and
excludes the channel in both cases, as a node and as an edge endpoint. A missing
node for a host is a smaller and more visible error than a false hub that every
talk in the corpus connects to.

**A failed extraction is marked, not swallowed.** `extract` returns `failed` with
the reason; `merge` surfaces the list on the graph; `build_graph.py` does not
treat a failed video as done, so the next run retries exactly those.

There is a second failure with no exception behind it: a talk whose own
frontmatter names a speaker cannot truthfully contain nobody. An empty answer
there is a failure wearing a result's clothes, and is marked as one.

## Consequences

- The graph now reports three counts instead of one — `rejected`, `irrelevant`,
  and a list of failed videos. That is the point: each names a distinct thing
  that went wrong, and a graph missing a quarter of its corpus says so on its
  face rather than quietly being smaller.
- **A long run is now resumable in the way it always claimed to be.** Caching
  failures as results was the bug that made the previous run unrecoverable
  without deleting state by hand. Same lesson as
  [ADR 0009](0009-batch-runs-resume.md), one layer up.
- `quote_supports` will pass some bad edges — a quote naming one endpoint while
  actually being about something else survives it. This is the accepted cost of
  not rejecting pronoun citations. The check is a floor, not a proof.
- Excluding the channel loses genuine host edges on interview channels, which is
  a real loss and the one this decision is least sure of. It is revisitable if a
  reliable person-vs-organisation signal appears; the metadata does not carry one
  today.
- All three defects were invisible in tests and obvious within minutes of reading
  real output. The tests added here are each pinned to the specific output that
  motivated them, so what they encode is a thing that actually happened rather
  than a thing that might.
