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


def person_note(person: dict, edges: list[dict], known: set[str],
                files: dict[str, str]) -> str:
    """One person's note: who they are, who they connect to, and where."""
    name = person["name"]
    lines = ["---", "tags:", "  - person", f"appearances: {person['videos']}", "---",
             "", f"# {name}", ""]

    if person.get("roles"):
        # Several descriptions of one person, from several transcripts. Kept as
        # a list rather than merged: they disagree sometimes, and which video
        # said what is the kind of thing this corpus exists to preserve.
        for role in person["roles"]:
            lines.append(f"- {role}")
        lines.append("")

    mine = [e for e in edges if e.get("from") == name or e.get("to") == name]
    if mine:
        lines += ["## Connections", ""]
        for edge in mine:
            outgoing = edge.get("from") == name
            other = edge.get("to") if outgoing else edge.get("from")
            phrase = PHRASING.get(edge.get("kind", ""), ("is connected to",) * 2)[
                0 if outgoing else 1]
            # A name with no note of its own stays plain text. An unresolved
            # link would put a ghost node on the graph for someone the corpus
            # only ever knew by a first name.
            shown = _link(note_name(other)) if other in known else f"**{other}**"
            lines.append(f"- {phrase} {shown}")
            if evidence := _quote(edge.get("evidence", "")):
                lines.append(f"  > {evidence}")
            where = edge.get("video", "")
            stamp = edge.get("timestamp", "")
            url = edge.get("url", "")
            if url and stamp:
                lines.append(f"  — [{stamp}]({url}) in {where}")
            elif where:
                lines.append(f"  — {where}")
            if edge.get("confidence") == "low":
                lines.append("  — *the transcript implies this rather than saying it*")
            lines.append("")

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


def write(corpus: str, out: str | None = None) -> dict:
    graph = load(corpus)
    folder = out or os.path.join(corpus, "People")
    os.makedirs(folder, exist_ok=True)
    files = transcripts(corpus)

    # Bare given names are scoped to one recording by graph.merge, because
    # "Barry" identifies someone inside that video and nobody in the world.
    # Thirty-five of the hundred and four here are that. A note each would put
    # thirty-five hubs on the graph joining videos that share nothing.
    people = [p for p in graph.get("people") or [] if not p.get("local")]
    known = {p["name"] for p in people}

    written = 0
    for person in people:
        path = os.path.join(folder, f"{note_name(person['name'])}.md")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(person_note(person, graph.get("edges") or [], known, files))
        written += 1

    linked = sum(1 for e in graph.get("edges") or []
                 if e.get("from") in known and e.get("to") in known)
    return {"people": written, "skipped": len(graph.get("people") or []) - written,
            "edges": linked, "folder": folder, "transcripts": len(files)}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print("\nusage: python obsidian.py <corpus dir> [--out FOLDER]")
        return 0
    corpus = args[0]
    out = args[args.index("--out") + 1] if "--out" in args else None
    result = write(corpus, out)
    print(f"{result['people']} people written to {result['folder']}")
    print(f"  {result['edges']} connections between them, each with its evidence")
    print(f"  {result['transcripts']} transcripts linked")
    if result["skipped"]:
        print(f"  {result['skipped']} skipped: known only by a first name, "
              f"so they identify someone inside one video and nobody across the corpus")
    print("\nOpen the corpus folder as an Obsidian vault to see the graph.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
