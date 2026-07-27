"""The people graph — offline. Never shells out.

The load-bearing test here is the evidence check. Everything else in this module
shapes data; that one decides whether a claim about two real people gets written
down. An edge is a much stronger statement than a transcript line, and it is
inferred from text where speech recognition has already mangled the names, so a
citation that does not resolve is worse than no citation — it looks like proof.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import graph
import ontology as ont
import speakers


@pytest.fixture(autouse=True)
def never_really_run(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("a test tried to run the real claude CLI")
    monkeypatch.setattr(subprocess, "run", forbidden)


@pytest.fixture
def answers(monkeypatch):
    """Queue what the model replies, and capture what it was asked."""
    def install(payload):
        seen = []
        monkeypatch.setattr(speakers, "_run",
                            lambda prompt, model=None: seen.append(prompt) or
                            (payload if isinstance(payload, str) else json.dumps(payload)))
        return seen
    return install


TALK = """\
---
title: Building a Chess Coach — Anant Dole
channel: AI Engineer
about: Anant Dole and Asbjorn Steinskog on chess engines.
url: https://www.youtube.com/watch?v=abcdefghijk
speakers: Anant Dole, Asbjorn Steinskog
---

# Building a Chess Coach

## Intro

[00:14](https://www.youtube.com/watch?v=abcdefghijk&t=14s) **Anant Dole**: this \
is where myself and my colleague Asbjorn currently work at Play Magnus

[01:30](https://www.youtube.com/watch?v=abcdefghijk&t=90s) he also founded a \
company and we joined later on
"""


@pytest.fixture
def talk(tmp_path):
    path = tmp_path / "chess.md"
    path.write_text(TALK, encoding="utf-8")
    return graph.parse_markdown(str(path))


# --------------------------------------------------------------------------
# reading our own markdown back
# --------------------------------------------------------------------------

def test_frontmatter_and_paragraphs_round_trip(talk):
    assert talk["title"] == "Building a Chess Coach — Anant Dole"
    assert talk["channel"] == "AI Engineer"
    assert talk["speakers"] == ["Anant Dole", "Asbjorn Steinskog"]
    assert len(talk["paragraphs"]) == 2
    assert talk["paragraphs"][0]["timestamp"] == "00:14"


def test_the_speaker_label_is_not_read_as_transcript_text(talk):
    """"**Anant Dole**: " is presentation. Leaving it in would let a name be
    "quoted" as evidence for a claim about the person saying it."""
    assert not talk["paragraphs"][0]["text"].startswith("**")
    assert "Anant Dole" not in talk["paragraphs"][0]["text"]


def test_timestamp_links_survive(talk):
    assert talk["paragraphs"][1]["url"].endswith("&t=90s")


def test_a_heading_is_not_a_paragraph(talk):
    assert not any(p["text"].startswith("#") for p in talk["paragraphs"])


# --------------------------------------------------------------------------
# the evidence check
# --------------------------------------------------------------------------

def test_a_real_quote_is_accepted(talk):
    assert graph.quote_is_real("myself and my colleague Asbjorn currently work",
                               talk["text"])


def test_punctuation_and_case_do_not_fail_an_honest_quote(talk):
    assert graph.quote_is_real("Myself and my colleague Asbjorn, currently work!",
                               talk["text"])


def test_an_invented_quote_is_refused(talk):
    """The failure this exists for: a fluent, plausible sentence nobody said."""
    assert not graph.quote_is_real(
        "Anant and Asbjorn founded the company together in 2019", talk["text"])


def test_a_paraphrase_is_refused(talk):
    """Close is not the same. A paraphrase cannot be checked against audio."""
    assert not graph.quote_is_real("myself and my colleague Asbjorn work there now",
                                   talk["text"])


def test_words_must_be_contiguous(talk):
    """Scattered words that all appear somewhere are not a quotation."""
    assert not graph.quote_is_real("chess coach myself company founded later",
                                   talk["text"])


@pytest.mark.parametrize("short", ["work at", "he also", "", "myself"])
def test_a_quote_too_short_to_prove_anything(short, talk):
    assert not graph.quote_is_real(short, talk["text"])


# --------------------------------------------------------------------------
# turning a reply into edges
# --------------------------------------------------------------------------

def edge(**over):
    base = {"from": "Anant Dole", "to": "Asbjorn Steinskog", "kind": "worked_with",
            "evidence": "myself and my colleague Asbjorn currently work",
            "timestamp": "00:14", "confidence": "high"}
    base.update(over)
    return base


def test_a_supported_edge_survives_with_its_provenance(answers, talk):
    answers({"people": [], "edges": [edge()]})
    got = graph.extract(talk)
    assert len(got["edges"]) == 1
    e = got["edges"][0]
    assert (e["from"], e["to"], e["kind"]) == ("Anant Dole", "Asbjorn Steinskog",
                                              "worked_with")
    assert e["url"].endswith("&t=14s")     # resolves to the second of video
    assert got["rejected"] == 0


def test_an_unsupported_edge_is_discarded_not_demoted(answers, talk):
    """Discarded, deliberately. A claim about two real people with a citation
    that does not resolve is worse than no claim: it reads as proof."""
    answers({"people": [], "edges": [edge(evidence="they founded it together")]})
    got = graph.extract(talk)
    assert got["edges"] == [] and got["rejected"] == 1


def test_an_unknown_relationship_kind_is_dropped(answers, talk):
    """A closed set, so the graph doesn't fragment into near-synonyms."""
    answers({"people": [], "edges": [edge(kind="is_associated_with")]})
    assert graph.extract(talk)["edges"] == []


def test_a_self_edge_is_dropped(answers, talk):
    answers({"people": [], "edges": [edge(to="Anant Dole")]})
    assert graph.extract(talk)["edges"] == []


def test_a_possessive_is_stripped_from_a_name(answers, talk):
    """Found in real output: "Magnus Carlsen's" arrived as a person, and would
    have become a second node for someone the graph already had."""
    answers({"people": [{"name": "Magnus Carlsen's", "role": "player"}], "edges": []})
    assert graph.extract(talk)["people"][0]["name"] == "Magnus Carlsen"


def test_a_garbled_reply_yields_an_empty_graph(answers, talk):
    answers("sorry, I can't help with that")
    got = graph.extract(talk)
    assert got["people"] == [] and got["edges"] == []


def test_the_corpus_is_offered_to_the_extractor(answers, talk):
    seen = answers({"people": [], "edges": []})
    graph.extract(talk, corpus={"tom oneill": "Tom O'Neill"})
    assert "Tom O'Neill" in seen[0]


# --------------------------------------------------------------------------
# merging many videos into one graph
# --------------------------------------------------------------------------

def result(people, video_id, edges=()):
    return {"people": [{"name": n, "role": "", "local": not ont.is_full_name(n)}
                       for n in people],
            "edges": list(edges), "rejected": 0, "video_id": video_id}


def test_one_person_is_one_node_across_videos():
    g = graph.merge([result(["Anant Dole"], "vid1"), result(["Anant Dole"], "vid2")])
    assert len(g["people"]) == 1
    assert g["people"][0]["videos"] == 2


def test_weight_counts_videos_not_mentions():
    """Appearing in five talks is what makes someone a recurring figure. Being
    named five times in one talk does not."""
    g = graph.merge([result(["Anant Dole", "Anant Dole", "Anant Dole"], "vid1")])
    assert g["people"][0]["videos"] == 1


def test_bare_given_names_do_not_merge_across_videos():
    """Forty talks contain several unrelated Maxes. Merging them on the string
    would invent a hub connecting things no one person connects."""
    g = graph.merge([result(["Max"], "vid1"), result(["Max"], "vid2")])
    assert len([p for p in g["people"] if p["name"] == "Max"]) == 2


def test_full_names_still_merge_across_videos():
    g = graph.merge([result(["Max Planck"], "vid1"), result(["Max Planck"], "vid2")])
    assert len(g["people"]) == 1


def test_the_fullest_spelling_wins():
    g = graph.merge([result(["Daniel Sheehan"], "v1"),
                     result(["Daniel Peter Sheehan"], "v2")])
    assert g["people"][0]["name"] == "Daniel Peter Sheehan"


def test_people_are_ordered_by_reach():
    g = graph.merge([result(["A Person", "B Person"], "v1"),
                     result(["A Person"], "v2")])
    assert [p["name"] for p in g["people"]] == ["A Person", "B Person"]


def test_rejections_are_totalled_across_the_corpus():
    """A model that starts inventing citations should be visible, not just
    quietly produce a smaller graph."""
    a, b = result([], "v1"), result([], "v2")
    a["rejected"], b["rejected"] = 2, 3
    assert graph.merge([a, b])["rejected"] == 5


def test_an_empty_corpus_is_not_a_crash():
    assert graph.merge([]) == {"people": [], "edges": [], "rejected": 0}


def test_the_graph_is_json_safe(tmp_path):
    g = graph.merge([result(["A Person"], "v1", [edge()])])
    graph.save(g, str(tmp_path / "g.json"))
    assert json.loads((tmp_path / "g.json").read_text(encoding="utf-8"))["people"]
