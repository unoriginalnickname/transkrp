"""Build a graph of people and how they connect, from a directory of transcripts.

A transcript answers "what was said". A corpus of them can answer something the
individual files cannot: who keeps appearing alongside whom, and on what basis.
That is a graph — entities, typed edges, properties — which is exactly the
structure Coyle's talk argues an agentic system should be checked against, so it
seemed fair to build one out of his own transcript.

**Every edge cites its evidence.** A relationship carries the sentence it came
from, the video, and the second of that video, so any claim in the graph resolves
back to audio. That is not decoration: an edge is a statement about two named,
real people, inferred from a transcript in which speech recognition has already
mangled proper nouns. "X is connected to Y" is a much stronger claim than "X said
this at 04:12", and it deserves a much stronger check.

The check is deterministic and it is the heart of this module: **an edge whose
evidence quote does not actually appear in the transcript is discarded.** Not
demoted — discarded. A model that paraphrases its evidence, or invents it, gets
no edge, because a citation that does not resolve is worse than no citation: it
looks like proof.

People are resolved through `ontology`, so one person is one node however the
transcripts spell them.

Requires the `claude` CLI. No API key.
"""

from __future__ import annotations

import json
import os
import re

import ontology
import speakers
import transkrp

# Relationship kinds worth distinguishing. A closed set, for the same reason
# Coyle enumerates order statuses: an open one fills with near-synonyms
# ("knows", "knew", "is associated with") that fragment the graph.
KINDS = {
    "interviewed": "one person interviewed or hosted the other",
    "worked_with": "colleagues, collaborators, or co-authors",
    "cites": "one person quotes, credits or draws on the other's work",
    "co_appeared": "both spoke in the same recording",
    "discussed": "one person talked about the other at length",
    "opposed": "public disagreement, rivalry or accusation",
}

PROMPT = f"""\
You extract a graph of people from a transcript.

List the people who appear — as speakers, or discussed by name — and the
relationships the transcript actually supports between them.

Rules that decide whether an edge survives:
- **Quote your evidence verbatim.** `evidence` must be an exact span copied from
  the transcript text below, 5 to 40 words. It is checked against the transcript
  and the edge is discarded if it does not match. Do not paraphrase, tidy,
  punctuate or correct it — copy it.
- Give the `[timestamp]` of the paragraph the quote came from.
- Only relationships the transcript states or plainly implies. Not what you know
  about these people from elsewhere; this graph is about what the recording says.
- Speech recognition mangles names. Use the spelling from the metadata where one
  exists.
- `confidence` is "high" when the transcript is explicit, "low" when you are
  inferring.

Relationship kinds — use only these:
{chr(10).join(f'- {k}: {v}' for k, v in KINDS.items())}

Reply with JSON and nothing else:

{{"people": [{{"name": "Full Name", "role": "what they are, in a few words"}}],
 "edges": [{{"from": "Full Name", "to": "Other Name", "kind": "interviewed",
            "evidence": "exact words copied from the transcript",
            "timestamp": "04:12", "confidence": "high"}}]}}
"""


def _words(text: str) -> list[str]:
    """Comparison form for evidence: the words, stripped of everything else."""
    return re.findall(r"[a-z0-9]+", text.lower())


def quote_supports(evidence: str, source: str, target: str) -> bool:
    """Does the quote actually mention one of the people it is evidence about?

    `quote_is_real` proves a citation is genuine. It does not prove the citation
    is *relevant*, and the difference is not academic: a real transcript produced
    `Ali Howard --interviewed--> Dex Horthy` cited by "And we've got Dax Raad,
    who you all know, CEO of Human Layer" — a true sentence about a third person,
    attached to a claim it says nothing about. It passed provenance and was
    nonsense.

    Requiring at least one endpoint to be named is deliberately weak: the other
    party is often a pronoun ("Dex covered this well in his talk"), so demanding
    both would reject sound edges. Weak, but it catches a quote that is about
    somebody else entirely.
    """
    words = set(_words(evidence))
    for name in (source, target):
        parts = _words(ontology.tidy_name(name))
        # Any name-word is enough: transcripts say "Dex", "Horthy", "Dex Horty".
        if any(p in words for p in parts if len(p) > 2):
            return True
        # And the ASR may have misheard it — "Horty" for "Horthy".
        if any(ontology._close(p, w) for p in parts if len(p) > 3 for w in words):
            return True
    return False


def quote_is_real(evidence: str, transcript_text: str) -> bool:
    """Does this quote actually occur in the transcript?

    Compared as a word sequence so punctuation and capitalisation the model may
    have tidied don't fail an otherwise honest citation — but the words
    themselves must appear, in order, contiguously. That is the line between a
    citation and a plausible-sounding invention.
    """
    needle = _words(evidence)
    if len(needle) < 4:            # too short to be evidence of anything
        return False
    hay = _words(transcript_text)
    return any(hay[i:i + len(needle)] == needle
               for i in range(len(hay) - len(needle) + 1))


def parse_markdown(path: str) -> dict:
    """Read one of our own transcripts back into a dict.

    Reading the markdown rather than re-fetching means the graph can be rebuilt
    from a corpus on disk, offline and free, however long ago it was gathered.
    """
    raw = open(path, encoding="utf-8").read()
    head, _, body = raw.partition("\n---\n")
    meta: dict[str, str] = {}
    for line in head.lstrip("-\n").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith(" "):
            meta.setdefault(key.strip(), value.split("  #")[0].strip())

    paragraphs = []
    for stamp, url, text in re.findall(
            r"^\[([\d:]+)\]\(([^)]*)\)\s*(?:\*\*(?:[^*]+)\*\*:\s*)?(.+)$", body, re.M):
        paragraphs.append({"timestamp": stamp, "url": url, "text": text.strip()})

    return {"title": meta.get("title", ""), "channel": meta.get("channel", ""),
            "url": meta.get("url", ""), "description": meta.get("about", ""),
            "speakers": [s.strip() for s in meta.get("speakers", "").split(",") if s.strip()],
            "paragraphs": paragraphs,
            "text": " ".join(p["text"] for p in paragraphs),
            "path": path}


def load(directory: str) -> list[dict]:
    """Every transcript in a corpus directory, oldest filename first."""
    out = []
    for name in sorted(os.listdir(directory)):
        if name.endswith(".md"):
            try:
                out.append(parse_markdown(os.path.join(directory, name)))
            except OSError:
                continue
    return out


def _is_the_channel(name: str, t: dict) -> bool:
    """Is this the uploading channel rather than a person?

    On an interview channel the host is a person and belongs in the graph; on a
    conference channel the same field is an organisation. Telling them apart
    automatically is not reliable, so the graph declines to guess and leaves the
    channel out — a missing node for a host is a smaller error than a false hub
    that every talk connects to.
    """
    channel = (t.get("channel") or "").strip()
    return bool(channel) and ontology._fold(name) == ontology._fold(channel)


def _stamp_url(t: dict, stamp: str) -> str:
    """The video at that second — reusing the link the transcript already has."""
    for p in t["paragraphs"]:
        if p["timestamp"] == stamp:
            return p["url"]
    return t.get("url", "")


def extract(t: dict, model: str | None = None,
            corpus: dict[str, str] | None = None) -> dict:
    """People and relationships from one transcript, evidence checked.

    Returns {"people": [...], "edges": [...], "rejected": n} where `rejected`
    counts edges whose quote could not be found — worth surfacing, because a
    model that starts inventing citations should be visible rather than quietly
    producing a smaller graph.
    """
    context = speakers._context(t, corpus)
    numbered = "\n\n".join(f"[{p['timestamp']}] {p['text']}" for p in t["paragraphs"])
    try:
        answer = speakers._parse(speakers._run(
            f"{PROMPT}\n\n{context}\n\n--- transcript ---\n\n{numbered}", model))
    except LookupError as e:
        # Marked failed, not empty. A swallowed error and a talk with nobody in
        # it produce the same empty result, and on a real 40-video run ten
        # consecutive transient failures were reported as ten findings of "no
        # people". A degraded run must not look like a completed one.
        return {"people": [], "edges": [], "rejected": 0, "failed": str(e),
                "video": t.get("title", ""),
                "video_id": transkrp.video_id(t.get("url", "")) or t.get("path", "")}

    known = ontology.known_people(t, corpus)
    people, edges, rejected, irrelevant = [], [], 0, 0

    for person in answer.get("people") or []:
        name = (person or {}).get("name")
        if not name:
            continue
        canonical, _ = ontology.canonicalise(ontology.tidy_name(name), known)
        if not canonical or _is_the_channel(canonical, t):
            # A conference channel is an entity but not a human, and left in it
            # became the graph's biggest hub: 14 of 107 edges hung off "AI
            # Engineer", asserting it had *interviewed* its own speakers.
            continue
        people.append({"name": canonical, "role": (person.get("role") or "").strip(),
                       # A bare given name identifies someone inside this
                       # recording and nowhere else; merge() keeps it local.
                       "local": not ontology.is_full_name(canonical)})

    for edge in answer.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        kind = edge.get("kind")
        evidence = (edge.get("evidence") or "").strip()
        if kind not in KINDS or not edge.get("from") or not edge.get("to"):
            continue
        if not quote_is_real(evidence, t["text"]):
            rejected += 1
            continue
        if not quote_supports(evidence, edge["from"], edge["to"]):
            # Genuine quote, wrong claim. Counted separately so the two kinds of
            # failure stay legible: one is a model inventing a citation, the
            # other is a model attaching a real one to the wrong pair.
            irrelevant += 1
            continue
        source, _ = ontology.canonicalise(ontology.tidy_name(edge["from"]), known)
        target, _ = ontology.canonicalise(ontology.tidy_name(edge["to"]), known)
        if not source or not target:
            continue
        if ontology._fold(source) == ontology._fold(target):
            continue                      # a self-edge says nothing
        if _is_the_channel(source, t) or _is_the_channel(target, t):
            continue
        stamp = str(edge.get("timestamp") or "")
        edges.append({
            "from": source, "to": target, "kind": kind,
            "evidence": evidence, "timestamp": stamp,
            "url": _stamp_url(t, stamp),
            "video": t.get("title", ""),
            "confidence": edge.get("confidence") or "low",
        })

    # A talk whose own frontmatter names a speaker cannot truthfully contain no
    # people. Empty here means the model returned nothing usable, which is a
    # failure wearing a result's clothes.
    failed = None
    if not people and t.get("speakers"):
        failed = "returned no people, though the transcript names a speaker"

    return {"people": people, "edges": edges, "rejected": rejected,
            "irrelevant": irrelevant,
            **({"failed": failed} if failed else {}),
            "video": t.get("title", ""),
            "video_id": transkrp.video_id(t.get("url", "")) or t.get("path", "")}


def merge(results: list[dict]) -> dict:
    """One graph from many transcripts, with people collapsed to one node each.

    A node's weight is how many distinct videos it appears in, which is the
    property that actually distinguishes a recurring figure from someone
    mentioned once — and is not the same as how often a name is said.
    """
    people: dict[str, dict] = {}
    edges: list[dict] = []

    for result in results:
        seen_here = set()
        for person in result.get("people") or []:
            key = ontology._fold(person["name"])
            if person.get("local"):
                # Scoped to its video, so forty transcripts' worth of Maxes
                # stay several people rather than becoming one hub that
                # connects things nobody connects.
                key = f"{key}@{result.get('video_id', id(result))}"
            node = people.setdefault(key, {"name": person["name"], "roles": [],
                                           "videos": 0, "appears_in": [],
                                           # Kept rather than discarded after
                                           # scoping: a consumer needs to know a
                                           # bare given name identifies someone
                                           # inside one recording and nowhere
                                           # else, or it will publish "Barry" as
                                           # a person in the world.
                                           "local": bool(person.get("local"))})
            # Keep the fullest spelling seen anywhere.
            if len(person["name"]) > len(node["name"]):
                node["name"] = person["name"]
            if person.get("role") and person["role"] not in node["roles"]:
                node["roles"].append(person["role"])
            if key not in seen_here:
                seen_here.add(key)
                node["videos"] += 1
                # Which recordings, not just how many. "Where have I seen this
                # person before" is the question a corpus exists to answer, and
                # the count alone cannot answer it.
                node["appears_in"].append({"video": result.get("video", ""),
                                           "video_id": result.get("video_id", "")})
        edges += result.get("edges") or []

    failures = [{"video": r.get("video", ""), "why": r["failed"]}
                for r in results if r.get("failed")]
    return {
        "people": sorted(people.values(), key=lambda p: (-p["videos"], p["name"])),
        "edges": edges,
        "rejected": sum(r.get("rejected", 0) for r in results),
        "irrelevant": sum(r.get("irrelevant", 0) for r in results),
        # Surfaced rather than absorbed: a graph missing a quarter of its corpus
        # should say so, not quietly be smaller.
        "failed": failures,
    }


def save(graph: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)
