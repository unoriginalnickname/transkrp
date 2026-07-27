"""The domain model, and the checks it enables. Deterministic, no subprocess.

The check on a probabilistic step must not itself be probabilistic, so none of
this calls a model — which is also what makes it testable in the first place.

The cases are Coyle's three errors, translated: a value outside the domain
("probably shipped" -> a speaker the metadata never heard of), two entities that
must stay distinct (buyer vs support rep -> host vs guest), and one thing
arriving twice (a second refund -> one person under three spellings).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ontology as ont
from test_transkrp import doc


def video(**over):
    base = dict(
        title="Why Agentic Systems Need Ontologies — Frank Coyle, UC Berkeley",
        channel="AI Engineer",
        description="Frank Coyle argues that most agent failures are ontological.",
    )
    base.update(over)
    return doc(**base)


def labels(*names):
    return {"labels": [None if n is None else
                       {"speaker": n, "confidence": "high"} for n in names]}


# --------------------------------------------------------------------------
# folding: the comparison key that makes variants collide
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Tom O'Neill", "Tom ONeill"),          # apostrophe lost
    ("Daniel Sheehan", "daniel  sheehan"),  # spacing
    ("Renée Descartes", "Renee Descartes"), # accent
])
def test_writing_variants_fold_together(a, b):
    """Folding normalises how a name is *written*."""
    assert ont._fold(a) == ont._fold(b)


def test_folding_does_not_survive_a_dropped_letter():
    """It normalises spelling, not mishearing — "O'Neil" for "O'Neill" gets
    past it, which is what `_close` exists for."""
    assert ont._fold("Tom O'Neill") != ont._fold("Tom O'Neil")


def test_close_merges_a_transcription_slip():
    assert ont._close("tom oneil", "tom oneill")


def test_close_refuses_to_fuse_two_people():
    """The bug this nearly shipped with. A whole-string ratio scores
    "tim oneill" against "tom oneill" at 0.89 and merges them; per word, the
    difference lands on a three-letter given name and is refused.

    A split person is visible in the data. A fused one is not.
    """
    assert not ont._close("tim oneill", "tom oneill")


def test_a_differing_given_name_is_evidence_not_noise():
    """Same end-to-end, through the resolver: sharing a surname is not enough."""
    name, grounded = ont.canonicalise("Tim ONeill", {"tom oneill": "Tom O'Neill"})
    assert (name, grounded) == ("Tim ONeill", False)


# --------------------------------------------------------------------------
# reading the people out of metadata
# --------------------------------------------------------------------------

def test_names_are_found_in_a_title():
    assert "Frank Coyle" in ont.names_in(
        "Why Agentic Systems Need Ontologies — Frank Coyle, UC Berkeley")


@pytest.mark.parametrize("text", [
    "Subscribe To The Channel",
    "New Episode",
    "In This Video",
])
def test_capitalised_phrases_are_not_mistaken_for_people(text):
    assert ont.names_in(text) == []


def test_a_name_does_not_span_a_sentence_boundary():
    """Found on a real description: "...Secret History of the Sixties. O'Neill
    and I went to Spahn Ranch" yielded "Sixties. O'Neill" as a person. It then
    sat in the known-people set, where a bare "O'Neill" resolved to it — making
    the garbage the canonical spelling for a real man.
    """
    found = ont.names_in("the Secret History of the Sixties. O'Neill and I went")
    assert not any("Sixties" in n for n in found)


def test_a_name_does_not_span_a_line_break():
    """Same bug, other axis: "American Alchemy\\nEMAIL" welded into one name."""
    found = ont.names_in("American Alchemy\nEMAIL Us Here")
    assert not any("\n" in n for n in found)
    assert "American Alchemy" in found


def test_over_collection_is_the_safe_direction():
    """An organisation slipping through costs a missed flag; a real person
    excluded would cost a wrongly-rejected attribution."""
    found = ont.names_in("Daniel Sheehan spoke about Brown Brothers Harriman.")
    assert "Daniel Sheehan" in found


def test_known_people_gathers_channel_title_and_description():
    known = ont.known_people(video())
    assert "AI Engineer" in known.values()
    assert "Frank Coyle" in known.values()


def test_the_longest_spelling_wins():
    """"Daniel Peter Sheehan" in the description beats a later bare "Sheehan"."""
    t = video(title="Sheehan on the CIA",
              description="Daniel Peter Sheehan is a lawyer. Sheehan says more.")
    assert "Daniel Peter Sheehan" in ont.known_people(t).values()


# --------------------------------------------------------------------------
# canonicalising a claimed name
# --------------------------------------------------------------------------

def test_a_mangled_spelling_resolves_to_the_metadata_spelling():
    """The correction that makes cross-episode joining possible at all."""
    known = {"tom oneill": "Tom O'Neill"}
    assert ont.canonicalise("tom o'neil", known) == ("Tom O'Neill", True)


def test_a_sentence_case_title_contributes_no_names():
    """Every word is capitalised in a headline, so the name regex happily
    reports "Every American Conspiracy" as a person. Only a credited tail
    ("- Frank Coyle", "ft. Nick Cook") is prose-like enough to trust."""
    t = video(title="The Man Involved In Every American Conspiracy",
              description="No names here.", channel="Jesse Michels")
    assert "Every American Conspiracy" not in ont.known_people(t).values()


def test_a_credited_title_does_contribute():
    t = video(title="\"We Have Antigravity UFOs!\" (ft. Nick Cook)", description="")
    assert "Nick Cook" in ont.known_people(t).values()


@pytest.mark.parametrize("name,first,last", [
    ("Daniel Peter Sheehan", "daniel", "sheehan"),   # middle name
    ("Dr. Jane Smith Jr.", "jane", "smith"),         # title and suffix
    ("Tom O'Neill, PhD", "tom", "oneill"),           # trailing credential
    ("Ludwig van der Berg", "ludwig", "van der berg"),  # particle
    ("Martin Luther King Jr", "martin", "king"),
])
def test_names_are_parsed_not_split(name, first, last):
    """Splitting on whitespace and taking the ends gave first="dr", last="jr"
    for "Dr. Jane Smith Jr." — wrong for any name with a title, suffix or
    particle, which is common in exactly this material."""
    assert ont._parts(name) == (first, last)


@pytest.mark.parametrize("claimed", [
    "Daniel Sheehan",      # middle name dropped
    "Dr. Daniel Sheehan",  # title added
])
def test_the_same_person_under_a_different_form_resolves(claimed):
    known = {ont._fold("Daniel Peter Sheehan"): "Daniel Peter Sheehan"}
    assert ont.canonicalise(claimed, known) == ("Daniel Peter Sheehan", True)


def test_parsing_does_not_reopen_the_fusing_hole():
    """A given name that disagrees still blocks the match, however the rest of
    the name is decorated."""
    known = {ont._fold("Tom O'Neill"): "Tom O'Neill"}
    assert ont.canonicalise("Tim O'Neill, PhD", known)[1] is False


def test_a_surname_alone_resolves_when_unambiguous():
    known = {"tom oneill": "Tom O'Neill"}
    assert ont.canonicalise("O'Neill", known) == ("Tom O'Neill", True)


def test_an_ambiguous_surname_is_left_alone():
    """Two known Smiths make "Smith" undecidable, and merging them would fuse
    two real people into one."""
    known = {"john smith": "John Smith", "jane smith": "Jane Smith"}
    name, grounded = ont.canonicalise("Smith", known)
    assert (name, grounded) == ("Smith", False)


def test_an_unknown_name_is_kept_but_flagged():
    """A guest introduced only in speech is real, just unverifiable. Deleting
    them would be worse than doubting them."""
    assert ont.canonicalise("Someone Else", {"frank coyle": "Frank Coyle"}) == (
        "Someone Else", False)


# --------------------------------------------------------------------------
# Coyle's error 1: a value outside the domain
# --------------------------------------------------------------------------

def test_an_ungrounded_speaker_is_flagged_and_demoted():
    """His "probably shipped" — a name the metadata has never heard of. It is
    kept, but never presented as certain."""
    fixed, violations = ont.check(video(), labels("Frank Coyle", "Hallucinated Person"))
    assert [v.kind for v in violations] == ["ungrounded_speaker"]
    assert violations[0].paragraphs == [2]
    assert fixed["labels"][1]["confidence"] == "low"
    assert fixed["labels"][0]["confidence"] == "high"   # the grounded one stands


def test_a_grounded_attribution_passes_clean():
    fixed, violations = ont.check(video(), labels("Frank Coyle", "Frank Coyle"))
    assert violations == []
    assert fixed["speakers"] == ["Frank Coyle"]


# --------------------------------------------------------------------------
# Coyle's error 2: entities that must stay distinct
# --------------------------------------------------------------------------

def test_the_host_and_guest_cannot_be_one_person():
    """His buyer-vs-support-rep disjointness."""
    t = video(channel="Jesse Michels",
              description="Our guest is Adam Michels, no relation.")
    _, violations = ont.check(t, labels("Jesse Michels", "Adam Michels"))
    assert any(v.kind == "host_guest_conflict" for v in violations)


# --------------------------------------------------------------------------
# Coyle's error 3: the same thing arriving twice
# --------------------------------------------------------------------------

def test_spelling_variants_are_merged_and_reported():
    """One person under three spellings would become three nodes in a graph.
    Merging is the fix; reporting it is the honesty."""
    t = video(description="Our guest is Tom O'Neill, author of Chaos.")
    fixed, violations = ont.check(t, labels("Tom O'Neill", "tom o'neil", "Tom ONeill"))
    assert fixed["speakers"] == ["Tom O'Neill"]
    assert all(l["speaker"] == "Tom O'Neill" for l in fixed["labels"])
    assert any(v.kind == "spelling_variants" for v in violations)


def test_a_merge_is_never_silent():
    t = video(description="Our guest is Tom O'Neill.")
    _, violations = ont.check(t, labels("Tom O'Neill", "tom o'neil"))
    detail = next(v.detail for v in violations if v.kind == "spelling_variants")
    assert "Tom O'Neill" in detail


# --------------------------------------------------------------------------
# cardinality and coverage
# --------------------------------------------------------------------------

def test_an_implausible_speaker_count_is_flagged():
    t = video(description="Guests: " + ", ".join(
        f"Person Number{i}" for i in range(1, 8)))
    _, violations = ont.check(t, labels(*[f"Person Number{i}" for i in range(1, 8)]))
    assert any(v.kind == "many_speakers" for v in violations)


def test_a_normal_interview_is_not_flagged_for_count():
    t = video(description="Frank Coyle speaks.")
    _, violations = ont.check(t, labels("AI Engineer", "Frank Coyle"))
    assert not any(v.kind == "many_speakers" for v in violations)


def test_sparse_coverage_is_flagged():
    """An attribution that attributes almost nothing is a failure wearing a
    success's clothes."""
    _, violations = ont.check(video(), labels("Frank Coyle", None, None, None))
    assert any(v.kind == "sparse_coverage" for v in violations)


def test_counts_are_recomputed_after_correction():
    fixed, _ = ont.check(video(), labels("Frank Coyle", None, "Frank Coyle"))
    assert fixed["attributed"] == 2 and fixed["unattributed"] == 1


def test_an_empty_attribution_is_not_a_crash():
    fixed, violations = ont.check(video(), {"labels": []})
    assert fixed["speakers"] == [] and violations == []


# --------------------------------------------------------------------------
# the corpus: identity that survives across episodes
# --------------------------------------------------------------------------

def test_a_corpus_name_grounds_a_later_mention():
    """The reason this exists. Episode 3 introduces a guest properly; episode 12
    only mentions them in passing, mangled by ASR, with nothing in its own
    description. Per-video knowledge leaves them ungrounded there — flagged,
    demoted, and unjoinable to their own earlier appearance.
    """
    later = video(title="A Different Episode", description="No guests listed.")
    corpus = {ont._fold("Tom O'Neill"): "Tom O'Neill"}

    alone, _ = ont.check(later, labels("tom o'neil"))
    with_corpus, violations = ont.check(later, labels("tom o'neil"), corpus)

    assert alone["labels"][0]["confidence"] == "low"        # unrecognised
    assert with_corpus["labels"][0]["speaker"] == "Tom O'Neill"
    assert not any(v.kind == "ungrounded_speaker" for v in violations)


def test_the_videos_own_metadata_outranks_the_corpus():
    """For the video it describes, the description is the better authority."""
    t = video(description="Our guest is Daniel Peter Sheehan.")
    corpus = {ont._fold("Daniel Peter Sheehan"): "D. P. Sheehan"}
    assert ont.known_people(t, corpus)[ont._fold("Daniel Peter Sheehan")] == \
        "Daniel Peter Sheehan"


def test_only_grounded_speakers_join_the_corpus():
    """An ungrounded name must not spread. Propagating one would make it
    self-confirming — the flag marking it doubtful vanishes at the exact moment
    it starts doing damage."""
    result = {"labels": [{"speaker": "Frank Coyle", "confidence": "high"},
                         {"speaker": "Invented Person", "confidence": "high"}]}
    carried = ont.confirmed(video(), result)
    assert "Frank Coyle" in carried.values()
    assert "Invented Person" not in carried.values()


def test_the_corpus_carries_the_canonical_spelling():
    t = video(description="Our guest is Tom O'Neill.")
    carried = ont.confirmed(t, labels("tom o'neil"))
    assert list(carried.values()) == ["Tom O'Neill"]


def test_an_empty_corpus_changes_nothing():
    plain, _ = ont.check(video(), labels("Frank Coyle"))
    with_empty, _ = ont.check(video(), labels("Frank Coyle"), {})
    assert plain["speakers"] == with_empty["speakers"]


# --------------------------------------------------------------------------
# what goes back to the model, and to the user
# --------------------------------------------------------------------------

def test_the_retry_hint_names_the_constraint_and_the_people():
    _, violations = ont.check(video(), labels("Hallucinated Person"))
    hint = ont.retry_hint(violations, ont.known_people(video()))
    assert "Hallucinated Person" in hint
    assert "Frank Coyle" in hint          # here is who actually exists


def test_no_violations_means_no_hint():
    assert ont.retry_hint([], {"a": "A"}) == ""


def test_the_summary_counts_by_kind():
    vs = [ont.Violation("ungrounded_speaker", "a"),
          ont.Violation("ungrounded_speaker", "b"),
          ont.Violation("many_speakers", "c")]
    assert ont.summarise(vs) == "ungrounded_speaker×2, many_speakers"


def test_an_empty_summary_for_a_clean_run():
    assert ont.summarise([]) == ""
