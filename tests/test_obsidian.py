"""Person notes for Obsidian: the links, and what must not happen to make them.

The property this file exists to protect is the one in the module docstring:
**the transcripts are never modified.** They are the verbatim record, they may
have been annotated by hand, and a generated rewrite of a file somebody edited
is a bad trade for a line on a graph. Everything else here is about the links
resolving — a link that doesn't is a ghost node, which is the graph equivalent of
a citation that doesn't check out.
"""

import json
import os

import pytest

import obsidian


GRAPH = {
    "people": [
        {"name": "Dex Horthy", "roles": ["CEO of HumanLayer"], "videos": 2,
         "local": False,
         "appears_in": [{"video": "The Great Loops Debate", "video_id": "c35YoMdnI78"},
                        {"video": "A talk not in this folder", "video_id": "zzzzzzzzzzz"}]},
        {"name": "Ian Livingstone", "roles": [], "videos": 1, "local": False,
         "appears_in": [{"video": "The Great Loops Debate", "video_id": "c35YoMdnI78"}]},
        {"name": "Barry", "roles": ["someone in the audience"], "videos": 1,
         "local": True,
         "appears_in": [{"video": "The Great Loops Debate", "video_id": "c35YoMdnI78"}]},
    ],
    "edges": [
        {"from": "Dex Horthy", "to": "Ian Livingstone", "kind": "opposed",
         "evidence": "So, I think first and foremost, I'm coming for you Dex.",
         "timestamp": "10:28", "confidence": "high",
         "url": "https://www.youtube.com/watch?v=c35YoMdnI78&t=628s",
         "video": "The Great Loops Debate"},
        {"from": "Dex Horthy", "to": "Barry", "kind": "worked_with",
         "evidence": "Barry and I put that together", "timestamp": "02:00",
         "confidence": "low", "url": "https://www.youtube.com/watch?v=c35YoMdnI78&t=120s",
         "video": "The Great Loops Debate"},
    ],
    "rejected": 1, "irrelevant": 0, "failed": [],
}


@pytest.fixture
def corpus(tmp_path):
    """A corpus folder: one transcript and a graph built from it."""
    (tmp_path / "the-great-loops-debate-c35YoMdnI78.md").write_text(
        "---\ntitle: The Great Loops Debate\n---\n\nverbatim, and not to be touched\n",
        encoding="utf-8")
    (tmp_path / "graph.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    return tmp_path


def notes(corpus):
    folder = corpus / "People"
    return {p.stem: p.read_text(encoding="utf-8") for p in folder.glob("*.md")}


# --- the property that matters ----------------------------------------------

def test_the_transcripts_are_never_modified(corpus):
    """The whole design rests on this: only new files, never a rewrite."""
    path = corpus / "the-great-loops-debate-c35YoMdnI78.md"
    before = path.read_bytes()
    obsidian.write(str(corpus))
    assert path.read_bytes() == before


def test_removing_the_folder_restores_the_corpus(corpus):
    """Reversible: the links are additive, so deleting them undoes everything."""
    before = sorted(p.name for p in corpus.iterdir())
    result = obsidian.write(str(corpus))
    for p in (corpus / "People").glob("*.md"):
        p.unlink()
    (corpus / "People").rmdir()
    assert sorted(p.name for p in corpus.iterdir()) == before
    assert result["people"] == 2


# --- who gets a note ---------------------------------------------------------

def test_a_first_name_only_person_gets_no_note(corpus):
    """"Barry" is someone inside one video and nobody across a corpus.

    A note would make a hub joining videos that share nothing but a common name.
    """
    obsidian.write(str(corpus))
    assert "Barry" not in notes(corpus)
    assert set(notes(corpus)) == {"Dex Horthy", "Ian Livingstone"}


def test_a_person_with_no_note_is_named_but_not_linked(corpus):
    """An unresolved [[Barry]] would put a ghost node on the graph."""
    obsidian.write(str(corpus))
    dex = notes(corpus)["Dex Horthy"]
    assert "[[Barry]]" not in dex
    assert "**Barry**" in dex          # still named, and still carries its quote
    assert "Barry and I put that together" in dex


# --- the links ---------------------------------------------------------------

def test_every_link_resolves_to_a_note_that_exists(corpus):
    """A link that doesn't resolve is this graph's version of a bad citation."""
    import re
    obsidian.write(str(corpus))
    written = notes(corpus)
    targets = set(written) | {p.stem for p in corpus.glob("*.md")}
    for name, body in written.items():
        for m in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", body):
            assert m.group(1) in targets, f"{name} links to missing {m.group(1)!r}"


def test_a_transcript_is_linked_by_filename_and_shown_by_title(corpus):
    """Obsidian resolves on filename; a reader wants the title."""
    obsidian.write(str(corpus))
    assert "[[the-great-loops-debate-c35YoMdnI78|The Great Loops Debate]]" \
        in notes(corpus)["Dex Horthy"]


def test_a_transcript_outside_the_folder_is_named_not_linked(corpus):
    """A corpus that has since moved should still read, without ghost nodes."""
    dex = (obsidian.write(str(corpus)), notes(corpus)["Dex Horthy"])[1]
    assert "A talk not in this folder" in dex
    assert "[[zzzzzzzzzzz" not in dex


# --- how an edge reads ------------------------------------------------------

def test_an_edge_reads_correctly_from_both_ends(corpus):
    """"opposed" on the other person's page is simply the wrong sentence."""
    obsidian.write(str(corpus))
    written = notes(corpus)
    assert "publicly disagreed with [[Ian Livingstone]]" in written["Dex Horthy"]
    assert "was publicly disagreed with by [[Dex Horthy]]" in written["Ian Livingstone"]


def test_the_evidence_and_its_timestamp_travel_into_the_note(corpus):
    """The point of the corpus: the claim is worth less than the sentence."""
    dex = (obsidian.write(str(corpus)), notes(corpus)["Dex Horthy"])[1]
    assert "> So, I think first and foremost, I'm coming for you Dex." in dex
    assert "[10:28](https://www.youtube.com/watch?v=c35YoMdnI78&t=628s)" in dex


def test_an_inferred_connection_says_so(corpus):
    obsidian.write(str(corpus))
    assert "implies this rather than saying it" in notes(corpus)["Dex Horthy"]


def test_a_confident_connection_is_not_hedged(corpus):
    obsidian.write(str(corpus))
    ian = notes(corpus)["Ian Livingstone"]
    assert "implies this" not in ian


# --- filenames ---------------------------------------------------------------

@pytest.mark.parametrize("name,safe", [
    ("Tom O'Neill", "Tom O'Neill"),          # apostrophes are fine
    ("Anna/Maria", "Anna-Maria"),            # a slash is a folder
    ("Who? What:", "Who- What"),             # trailing punctuation goes
    ("[bracketed]", "bracketed"),            # brackets break the link syntax
])
def test_a_name_becomes_a_filename_obsidian_accepts(name, safe):
    assert obsidian.note_name(name) == safe


def test_a_name_of_nothing_but_punctuation_still_gets_a_filename():
    assert obsidian.note_name("///") == "unnamed"


# --- failure -----------------------------------------------------------------

def test_a_corpus_with_no_graph_says_what_to_run(tmp_path):
    with pytest.raises(SystemExit, match="build_graph"):
        obsidian.write(str(tmp_path))


def test_rerunning_replaces_rather_than_duplicates(corpus):
    obsidian.write(str(corpus))
    first = notes(corpus)["Dex Horthy"]
    obsidian.write(str(corpus))
    assert notes(corpus)["Dex Horthy"] == first
    assert len(notes(corpus)) == 2
