"""Turn a corpus's people graph into notes Obsidian can draw.

Obsidian already is the viewer: search, backlinks, panes, and a graph view that
is better than anything worth writing here. What it cannot do is read
`graph.json` — it draws a line between two notes only when one links to the
other with `[[double brackets]]`. So a corpus of forty transcripts opens as forty
disconnected dots, with every relationship the graph found sitting invisible in
a file beside them.

This writes the missing links, and nothing else.

**It never touches the transcripts.** Every link lives in a new note under
`People/`, which links out to the transcripts and to other people. Obsidian's
graph draws the same lines either way — a link is a link, whichever end holds it
— so there is no reason to rewrite forty documents that are already correct, and
several reasons not to: they are the verbatim record, they may be edited by
hand, and a generated rewrite of a file somebody has annotated is a bad trade for
a line on a chart. Delete the `People/` folder and the corpus is exactly as it
was.

**Every connection keeps its evidence.** The quote and the link to the second of
video travel into the note, because an assertion that two people are connected is
worth much less than the sentence that says so — the same argument
`graph.py` makes for keeping the citation, carried one step further to where a
person will actually read it.

    python obsidian.py corpus/ai-engineer
"""

from __future__ import annotations

import json
import os
import re
import sys

# How an edge reads from each end. A relationship is directional and a note that
# says "interviewed" on the guest's page is simply wrong, so both readings are
# spelled out rather than derived.
PHRASING = {
    "interviewed": ("interviewed", "was interviewed by"),
    "worked_with": ("worked with", "worked with"),
    "cites": ("cites", "is cited by"),
    "co_appeared": ("appeared alongside", "appeared alongside"),
    "discussed": ("talked about", "was talked about by"),
    "opposed": ("publicly disagreed with", "was publicly disagreed with by"),
}

# Windows and Obsidian both refuse these in a filename.
_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]')

# Only read by the weighted-graph plugin, which draws thickness from `[[X]]::n`.
# Stock Obsidian has no per-link weight at all — the thickness slider is global —
# so the hierarchy that works everywhere is the one in the headings and tags.
WEIGHT = {"high": 3, "low": 1}


def note_name(person: str) -> str:
    """A filename for a person that Obsidian will accept and still recognise.

    Trailing dots and dashes go too: a name of nothing but punctuation would
    otherwise substitute its way to "---", which is a legal filename and a
    useless note.
    """
    return _UNSAFE.sub("-", person).strip(". -") or "unnamed"


def load(corpus: str) -> dict:
    path = os.path.join(corpus, "graph.json")
    if not os.path.exists(path):
        raise SystemExit(f"no graph.json in {corpus}. Run: python build_graph.py {corpus}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def transcripts(corpus: str) -> dict[str, str]:
    """video id -> the note name Obsidian knows that transcript by.

    The id is the last thing in every filename this tool writes, which is what
    makes the join possible without opening forty files.
    """
    found = {}
    for name in os.listdir(corpus):
        if not name.endswith(".md"):
            continue
        stem = name[:-3]
        if m := re.search(r"-([\w-]{11})$", stem):
            found[m.group(1)] = stem
    return found


def _link(target: str, shown: str | None = None) -> str:
    return f"[[{target}|{shown}]]" if shown and shown != target else f"[[{target}]]"


def _quote(text: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def tier(person: dict) -> str:
    """How much the corpus actually knows about someone.

    Measured on the corpus this was built against, recurrence is not the useful
    axis: 4 people appear in more than one video and **no edge joins two of
    them**, so a hierarchy built on it would put nothing in its top tier. It is
    still the honest label for a node — a person seen three times is a different
    kind of entry from one seen once — and the tier that carries the weight is
    the confidence split on the edges, which is 63/11.
    """
    if person.get("local"):
        return "person/local"
    return "person/recurring" if person.get("videos", 0) >= 2 else "person/named"


def _scoped(name: str, edge: dict) -> str:
    """A first-name-only person, named so two of them never become one node.

    This is the weakest tier, and it is a *link* rather than bold text — which
    reverses part of ADR 0016. The reason that decision gave still stands: an
    unresolved `[[Barry]]` merges every Barry in the corpus into one hub joining
    videos that share nothing. Scoping it to the recording defeats exactly that,
    and buys the thing Obsidian gives for free — an unresolved link draws a
    smaller, dimmer node, which is the only native rendering of "weaker" there
    is. See ADR 0017.
    """
    # Scoped by video id, not title. A truncated title is prettier on hover and
    # wrong: two talks from one conference share their first forty characters
    # often, and a collision merges exactly the two people this is separating.
    # The id is also what every transcript filename here already ends with.
    m = re.search(r"[?&]v=([\w-]{11})", edge.get("url", "") or "")
    scope = m.group(1) if m else _quote(edge.get("video", ""), 40)
    label = f"{name} ({scope})" if scope else name
    return _link(note_name(label), name)


def _connection(edge: dict, name: str, known: set[str], weights: bool) -> list[str]:
    outgoing = edge.get("from") == name
    other = edge.get("to") if outgoing else edge.get("from")
    phrase = PHRASING.get(edge.get("kind", ""), ("is connected to",) * 2)[
        0 if outgoing else 1]
    shown = _link(note_name(other)) if other in known else _scoped(other, edge)
    # For the weighted-graph plugin, which is the only way to get a genuinely
    # thicker line: stock Obsidian has no per-link weight, so this is inert
    # unless that plugin is installed, and harmless when it isn't.
    if weights:
        shown += f"::{WEIGHT.get(edge.get('confidence'), 1)}"
    out = [f"- {phrase} {shown}"]
    if evidence := _quote(edge.get("evidence", "")):
        out.append(f"  > {evidence}")
    where, stamp, url = edge.get("video", ""), edge.get("timestamp", ""), edge.get("url", "")
    if url and stamp:
        out.append(f"  — [{stamp}]({url}) in {where}")
    elif where:
        out.append(f"  — {where}")
    out.append("")
    return out


def person_note(person: dict, edges: list[dict], known: set[str],
                files: dict[str, str], weights: bool = False) -> str:
    """One person's note: who they are, who they connect to, and where."""
    name = person["name"]
    lines = ["---", "tags:", "  - person", f"  - {tier(person)}",
             f"appearances: {person['videos']}", "---", "", f"# {name}", ""]

    if person.get("roles"):
        # Several descriptions of one person, from several transcripts. Kept as
        # a list rather than merged: they disagree sometimes, and which video
        # said what is the kind of thing this corpus exists to preserve.
        for role in person["roles"]:
            lines.append(f"- {role}")
        lines.append("")

    mine = [e for e in edges if e.get("from") == name or e.get("to") == name]
    # Split by what the transcript actually did. "The model was inferring" and
    # "the speaker said it out loud" are different claims, and running them
    # together under one heading is how the weaker one gets read as the stronger.
    stated = [e for e in mine if e.get("confidence") != "low"]
    implied = [e for e in mine if e.get("confidence") == "low"]

    if stated:
        lines += ["## Connections", ""]
        for edge in stated:
            lines += _connection(edge, name, known, weights)

    if implied:
        lines += ["## Possible connections", "",
                  "*The transcript implies these rather than saying them.*", ""]
        for edge in implied:
            lines += _connection(edge, name, known, weights)

    appearances = person.get("appears_in") or []
    if appearances:
        lines += ["## Appears in", ""]
        for spot in appearances:
            stem = files.get(spot.get("video_id", ""))
            title = spot.get("video") or spot.get("video_id") or "unknown"
            # Link the transcript when it is in this folder; otherwise name it,
            # so a graph built from a corpus that has since moved still reads.
            lines.append(f"- {_link(stem, title) if stem else title}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write(corpus: str, out: str | None = None, weights: bool = False) -> dict:
    graph = load(corpus)
    folder = out or os.path.join(corpus, "People")
    os.makedirs(folder, exist_ok=True)
    files = transcripts(corpus)

    # A note only for people the corpus can identify across it. Bare given names
    # still get *linked* — scoped to their recording, so they land as dim
    # unresolved nodes instead of one merged hub — but a note would promote them
    # to a first-class entry they haven't earned.
    people = [p for p in graph.get("people") or [] if not p.get("local")]
    known = {p["name"] for p in people}
    edges = graph.get("edges") or []

    written = 0
    for person in people:
        path = os.path.join(folder, f"{note_name(person['name'])}.md")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(person_note(person, edges, known, files, weights))
        written += 1

    strong = sum(1 for e in edges if e.get("confidence") != "low")
    return {"people": written, "folder": folder, "transcripts": len(files),
            "edges": len(edges), "stated": strong, "implied": len(edges) - strong,
            "recurring": sum(1 for p in people if p.get("videos", 0) >= 2),
            "local": len(graph.get("people") or []) - written}


def main(argv: list[str] | None = None) -> int:
    # A Windows console defaults to cp1252, which cannot encode an arrow or an
    # em dash — and every name in this corpus is somebody's, so the output is
    # full of characters it has no idea about. Same guard transkrp.main uses.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print("\nusage: python obsidian.py <corpus dir> [--out FOLDER] [--weights]")
        print("\n  --weights   also emit [[Name]]::n for the weighted-graph plugin,")
        print("              which is the only way to get a genuinely thicker line.")
        return 0
    corpus = args[0]
    out = args[args.index("--out") + 1] if "--out" in args else None
    result = write(corpus, out, weights="--weights" in args)
    print(f"{result['people']} people written to {result['folder']}, "
          f"{result['transcripts']} transcripts linked")
    print(f"  {result['stated']} connections the transcript states")
    print(f"  {result['implied']} it only implies, under their own heading")
    print(f"  {result['local']} people known by a first name: linked but scoped to "
          f"their recording, so they stay dim and separate")
    print("\nOpen the corpus folder as a vault. To see the tiers, add colour groups")
    print("in Graph view → Groups:  tag:#person/recurring  and  tag:#person/named")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
