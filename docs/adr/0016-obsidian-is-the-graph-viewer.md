# 0016. Obsidian is the graph viewer, and the transcripts stay untouched

- Status: Accepted
- Date: 2026-08-06

## Context

`graph.py` builds a real graph — 104 people and 74 evidence-carrying edges over a
40-video corpus — and writes it to `graph.json`. Nothing could see it. Opening
the corpus in Obsidian, which is where these documents are actually read, gave
forty disconnected dots: **zero `[[links]]` across all forty transcripts**, so
Obsidian had nothing to draw.

The obvious response is a viewer — a web page with a force-directed graph, click
a person, read their edges. A version of it was mocked up. The argument against
it is that Obsidian already does this, better, and is already open: search,
backlinks, local graph, panes, and a graph view nobody here is going to beat.
Writing one would be competing with an incumbent that has won.

So the question became not "how do we show the graph" but "what is the smallest
thing that lets the tool that already shows graphs show this one".

## Decision

**Emit `[[links]]` and let Obsidian draw. No viewer.**

**Every link lives in a new note; the transcripts are never rewritten.** Person
notes go in `People/`, and they link outward — to the transcripts each person
appears in, and to the other people they connect to. Obsidian's graph draws the
same line whichever end holds the link, so rewriting forty documents buys
nothing and costs plenty: they are the verbatim record, they may have been
annotated by hand, and replacing a file somebody edited to gain a line on a chart
is a bad trade. Deleting `People/` returns the corpus byte-for-byte, which is
tested.

**Each connection carries its evidence into the note** — the quote, and the
timestamp link to the second of video. This is
[ADR 0012](0012-validate-the-model-against-a-domain-model.md)'s argument carried
one step further: the graph already refuses an edge whose citation doesn't
resolve, and the citation is worth more than the assertion, so it should be in
front of the person reading rather than left behind in JSON.

**A person known only by a first name gets no note.** `graph.merge` already
scopes bare given names to one video, because "Barry" identifies someone inside
that recording and nobody across a corpus. Thirty-five of the hundred and four
here are that. Giving each a note would add thirty-five hubs joining videos that
share nothing but a common first name — precisely the false connection the
scoping exists to prevent. They keep their evidence quote in the note that
mentions them, as bold text rather than a link.

**A name with no note is never linked.** An unresolved `[[Barry]]` puts a ghost
node on the graph, which looks exactly like a real person until clicked. Same
failure as a citation that doesn't resolve, one layer up.

## Consequences

`merge()` now records `appears_in` (title and video id) and keeps the `local`
flag on each person rather than discarding it after scoping. Both were derivable
before only by re-reading the per-video parts file. Re-merging is free — the
expensive per-video extraction is cached in `.graph-parts.jsonl` — so the
enriched graph was rebuilt over the existing corpus with no model calls.

Measured on that corpus: 69 notes, 152 links, **0 unresolved**, all 40
transcripts reachable from a person note, 48 of 69 people connected to at least
one other.

The corpus folder is now openable directly as a vault. That is the whole
interface: pull transcripts, run `obsidian.py`, open the folder. No server, no
page, nothing to keep running.

What this doesn't do is keep itself current. `obsidian.py` is a step you run
after `build_graph.py`, and re-running replaces the notes rather than merging
into them — so a person note edited by hand will be overwritten. That is the
cost of them being generated artifacts, and it is why they live in their own
folder rather than among the documents somebody might annotate.
