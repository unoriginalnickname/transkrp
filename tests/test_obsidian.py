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


def test_a_first_name_is_linked_but_scoped_to_its_recording(corpus):
    """The weakest tier: a dim unresolved node that cannot merge with another.

    A bare `[[Barry]]` would collapse every Barry in the corpus into one hub.
    Scoped, each stays attached to the one video it is real in, and Obsidian
    renders an unresolved link smaller and dimmer — the only native "weaker".
    """
    obsidian.write(str(corpus))
    dex = notes(corpus)["Dex Horthy"]
    assert "[[Barry]]" not in dex
    assert "[[Barry (c35YoMdnI78)|Barry]]" in dex
    assert "Barry and I put that together" in dex


def test_two_people_sharing_a_first_name_never_become_one_node(tmp_path):
    """The whole reason for scoping. Same name, two videos, two nodes."""
    graph = json.loads(json.dumps(GRAPH))
    graph["edges"].append({
        "from": "Ian Livingstone", "to": "Barry", "kind": "worked_with",
        "evidence": "Barry had the other half of it", "timestamp": "01:00",
        "confidence": "high", "video": "A Different Talk",
        "url": "https://www.youtube.com/watch?v=DIFFERENT11&t=60s"})
    (tmp_path / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    obsidian.write(str(tmp_path))
    written = notes(tmp_path)
    assert "[[Barry (c35YoMdnI78)|Barry]]" in written["Dex Horthy"]
    assert "[[Barry (DIFFERENT11)|Barry]]" in written["Ian Livingstone"]


def test_a_title_is_never_used_to_scope_when_an_id_is_available(corpus):
    """Two talks from one conference share their first forty characters."""
    obsidian.write(str(corpus))
    assert "(The Great Loops Debate" not in notes(corpus)["Dex Horthy"]


# --- the hierarchy ----------------------------------------------------------

def test_people_are_tagged_by_how_well_the_corpus_knows_them(corpus):
    """The tag is what graph view's colour groups can actually query."""
    obsidian.write(str(corpus))
    written = notes(corpus)
    assert "- person/recurring" in written["Dex Horthy"]      # 2 videos
    assert "- person/named" in written["Ian Livingstone"]     # 1 video


def test_stated_and_implied_connections_are_kept_apart(corpus):
    """Run together under one heading, the weaker claim reads as the stronger."""
    obsidian.write(str(corpus))
    dex = notes(corpus)["Dex Horthy"]
    stated, _, implied = dex.partition("## Possible connections")
    assert "## Connections" in stated
    assert "Ian Livingstone" in stated        # high confidence
    assert "Barry" in implied                 # low confidence
    assert "Barry" not in stated


def test_a_note_with_only_confident_edges_has_no_possible_heading(corpus):
    obsidian.write(str(corpus))
    assert "## Possible connections" not in notes(corpus)["Ian Livingstone"]


def test_weights_are_off_by_default(corpus):
    """Stock Obsidian shows `::3` as literal text, so it must be asked for."""
    obsidian.write(str(corpus))
    assert "::" not in notes(corpus)["Dex Horthy"]


def test_weights_grade_the_link_when_asked(corpus):
    obsidian.write(str(corpus), weights=True)
    dex = notes(corpus)["Dex Horthy"]
    assert "[[Ian Livingstone]]::3" in dex     # stated
    assert "::1" in dex                        # implied


# --- the links ---------------------------------------------------------------

def test_no_link_is_unresolved_except_a_deliberately_scoped_one(corpus):
    """An accidental dangling link is a ghost node; a scoped one is the weak tier.

    The distinction is the whole hierarchy: a link either points at a real note,
    or it is a first-name person carrying the video id that keeps them apart.
    Anything else is a bug that looks like a person until you click it.
    """
    import re
    obsidian.write(str(corpus))
    written = notes(corpus)
    targets = set(written) | {p.stem for p in corpus.glob("*.md")}
    for name, body in written.items():
        for m in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", body):
            target = m.group(1)
            if target in targets:
                continue
            assert re.search(r" \([\w-]{11}\)$", target), \
                f"{name} has a dangling link to {target!r}"


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
    """Hedged once, under its own heading, rather than on every line."""
    obsidian.write(str(corpus))
    assert "implies these rather than saying them" in notes(corpus)["Dex Horthy"]


def test_a_confident_connection_is_not_hedged(corpus):
    obsidian.write(str(corpus))
    ian = notes(corpus)["Ian Livingstone"]
    assert "implies" not in ian


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
