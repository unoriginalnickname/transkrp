# 0017. Three tiers of link strength, encoded where Obsidian can see them

- Status: Accepted
- Amends [0016](0016-obsidian-is-the-graph-viewer.md)
- Date: 2026-08-06

## Context

[ADR 0016](0016-obsidian-is-the-graph-viewer.md) got the links into Obsidian, and
they all arrived equal. But the edges are not equal: 63 of the 74 are things a
speaker said outright, and 11 the model inferred. Drawing them identically
publishes a guess with the same weight as a quote — the failure
[ADR 0012](0012-validate-the-model-against-a-domain-model.md) exists to prevent,
reintroduced at the presentation layer.

**Obsidian has no per-link weight.** Confirmed before designing anything: the
graph view's thickness control is a global slider, and weighted edges are a
long-standing open feature request. The only plugin that does it
(`obsidian-weighted-graph`, `[[Note]]::n` syntax) is not something to require.

What Obsidian *does* give natively is two things: colour groups, which query
tags, paths and properties to colour **nodes**; and a built-in visual difference
between a resolved link and an unresolved one, which renders smaller and dimmer.

The data also had an opinion. Tiering by recurrence — the obvious axis — turns
out to be useless here: only 4 people appear in more than one video, and **no
edge joins two of them**. A hierarchy built on it would have an empty top tier.

## Decision

**Three tiers, each encoded where Obsidian can actually act on it.**

| Tier | What it means | How it is encoded |
|---|---|---|
| Stated | the speaker said it | `## Connections`, a resolved link |
| Implied | the model inferred it | `## Possible connections`, hedged once at the heading |
| Local | known only by a first name | a *scoped* unresolved link — dim, small |

**Confidence splits the headings, not the lines.** The previous version hedged
each low-confidence edge with an italic note at the end of it, which reads as a
footnote on an otherwise equal claim. A separate heading makes the weaker set
weaker at a glance, and states the caveat once instead of eleven times.

**This reverses 0016's rule that a person with no note is never linked.** That
decision was right about the danger and wrong about the remedy. An unresolved
`[[Barry]]` does put a ghost node on the graph, and worse, *merges every Barry in
the corpus into one hub joining videos that share nothing*. But scoping the link
to its recording — `[[Barry (c35YoMdnI78)]]`, displayed as "Barry" — defeats
exactly that, and buys the one native rendering of "weaker" that exists. Measured
on the corpus: 29 distinct weak nodes, where unscoped would have collapsed to 26.
Three separate people would have been silently merged.

**Scoped by video id, not title.** A truncated title is prettier on hover and
wrong: talks from one conference routinely share their first forty characters, and
a collision merges precisely the two people the scoping separates. The id is also
what every transcript filename here already ends with.

**Tags carry the node tier** (`person/recurring`, `person/named`,
`person/local`), because a tag is what a colour group can query. Recurrence is a
weak axis on this corpus but an honest label — a person seen three times is a
different kind of entry from one seen once — and it costs nothing to record for a
corpus where it will eventually mean more.

**`--weights` is opt-in.** It appends `::3` / `::1` for the weighted-graph
plugin, which is the only way to get a genuinely thicker line. Off by default
because stock Obsidian renders the suffix as literal text in the note body, and a
document that reads worse for a feature you may not have installed is a bad
default.

## Consequences

The hierarchy a person sees without installing anything is: two node weights
(solid vs dim) and two headings. That is less than true weighted edges and it is
what the platform offers; pretending otherwise would mean shipping a viewer,
which 0016 already decided against for good reasons.

Colour groups have to be configured by hand once, in Graph view → Groups. The
tags make it a two-query setup, and it cannot be automated — Obsidian stores
graph settings in the vault's `.obsidian/`, which is the user's, not ours.

Measured after the change: 152 resolved links, 34 scoped weak links, **0
dangling**. The test suite asserts that distinction directly — a link either
resolves or carries an 11-character id, and anything else fails.
