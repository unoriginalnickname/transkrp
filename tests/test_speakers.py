"""Speaker attribution — offline. Never shells out to a real `claude`.

Every test stubs the subprocess. That matters more than usual here: a test that
actually invoked the CLI would be slow, would need the user logged in, and would
spend their plan's quota to assert something about our own parsing.

What's covered is our side — that names come off the metadata rather than the
mangled transcript, that a label the model won't commit to stays unattributed,
that batch numbering can't smear labels onto neighbouring paragraphs, and that a
missing CLI says so once rather than forty times.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import speakers
import transkrp as tk
from test_transkrp import doc


@pytest.fixture(autouse=True)
def never_really_run(monkeypatch):
    """Belt and braces: an un-stubbed test fails loudly instead of shelling out."""
    def forbidden(*a, **k):
        raise AssertionError("a test tried to run the real claude CLI")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(speakers.shutil, "which", lambda name: "/usr/bin/claude")


@pytest.fixture
def replies(monkeypatch):
    """Queue up what `claude` says back, and record what it was asked."""
    def install(*payloads):
        seen = []

        def fake_run(prompt, model=None):
            seen.append({"prompt": prompt, "model": model})
            out = payloads[min(len(seen) - 1, len(payloads) - 1)] if payloads else {}
            if isinstance(out, Exception):
                raise out
            return out if isinstance(out, str) else json.dumps(out)

        monkeypatch.setattr(speakers, "_run", fake_run)
        return seen
    return install


def interview(n=4):
    return doc(
        title="The Man Involved In Every American Conspiracy",
        channel="Jesse Michels",
        upload_date="2024-08-22",
        description="Daniel Peter Sheehan is a Harvard trained constitutional "
                    "and public interest lawyer. " + "sponsor filler. " * 300,
        paragraphs=[{"start_ms": i * 1000, "timestamp": "00:00", "turn": 0,
                     "text": f"paragraph {i} words"} for i in range(n)],
    )


# --------------------------------------------------------------------------
# no API key anywhere
# --------------------------------------------------------------------------

def test_no_anthropic_sdk_import():
    """The whole point of this rewrite: it runs on the `claude` command, so the
    module must not reach for the API SDK even if one happens to be installed."""
    src = Path(speakers.__file__).read_text(encoding="utf-8")
    assert "import anthropic" not in src
    assert "ANTHROPIC_API_KEY" not in src


def test_the_command_is_claude_print(monkeypatch):
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"], calls["input"] = cmd, kw.get("input")
        return subprocess.CompletedProcess(cmd, 0, '{"speakers":[],"labels":[]}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    speakers._run("ask", None)
    assert calls["cmd"][:2] == ["claude", "-p"]
    assert "--output-format" in calls["cmd"]
    assert calls["input"] == "ask"


def test_a_model_is_passed_through_when_given(monkeypatch):
    calls = {}
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (
        calls.update(cmd=cmd), subprocess.CompletedProcess(cmd, 0, "{}", ""))[1])
    speakers._run("ask", "claude-haiku-4-5")
    assert "--model" in calls["cmd"] and "claude-haiku-4-5" in calls["cmd"]


def test_a_missing_cli_is_named_not_guessed(monkeypatch):
    monkeypatch.setattr(speakers.shutil, "which", lambda name: None)
    with pytest.raises(speakers.NotAvailable, match="claude"):
        speakers._run("ask", None)


def test_a_nonzero_exit_is_reported(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
                        subprocess.CompletedProcess(cmd, 1, "", "not logged in"))
    with pytest.raises(LookupError, match="not logged in"):
        speakers._run("ask", None)


def test_a_hang_times_out(monkeypatch):
    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, speakers.TIMEOUT)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(LookupError, match="did not answer"):
        speakers._run("ask", None)


# --------------------------------------------------------------------------
# reading a CLI reply, which is prose-shaped rather than an API envelope
# --------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    '{"speakers": ["A"], "labels": []}',
    '```json\n{"speakers": ["A"], "labels": []}\n```',
    '```\n{"speakers": ["A"], "labels": []}\n```',
    'Here you go:\n\n{"speakers": ["A"], "labels": []}',
    '{"speakers": ["A"], "labels": []}\n\nLet me know if you need more.',
])
def test_json_is_recovered_from_its_packaging(reply):
    """`-p` returns assistant text, which can arrive fenced or wrapped in a
    sentence. Throwing away a good answer over its packaging would be silly."""
    assert speakers._parse(reply) == {"speakers": ["A"], "labels": []}


@pytest.mark.parametrize("reply", ["", "no json here", "{broken", "[1,2,3]"])
def test_unparseable_replies_yield_nothing(reply):
    assert speakers._parse(reply) == {}


# --------------------------------------------------------------------------
# what the model is told
# --------------------------------------------------------------------------

def test_the_description_is_sent(replies):
    """The reason attribution can produce a *name*: the ASR says "Danny shean",
    the description says "Daniel Peter Sheehan"."""
    seen = replies({"speakers": [], "labels": []})
    speakers.attribute(interview())
    assert "Daniel Peter Sheehan" in seen[0]["prompt"]


def test_the_channel_is_offered_as_the_host(replies):
    seen = replies({"speakers": [], "labels": []})
    speakers.attribute(interview())
    assert "Jesse Michels" in seen[0]["prompt"]


def test_the_sponsor_tail_is_trimmed(replies):
    """Descriptions run to thousands of characters of links; the guest is named
    at the top and the rest is prompt nobody is buying anything with."""
    seen = replies({"speakers": [], "labels": []})
    speakers.attribute(interview())
    assert len(seen[0]["prompt"]) < 5000


# --------------------------------------------------------------------------
# reading the answer back
# --------------------------------------------------------------------------

def test_labels_land_on_their_paragraphs(replies):
    replies({"speakers": ["Jesse Michels", "Daniel Sheehan"],
             "labels": [{"n": 1, "speaker": "Jesse Michels", "confidence": "high"},
                        {"n": 2, "speaker": "Daniel Sheehan", "confidence": "high"},
                        {"n": 3, "speaker": "Daniel Sheehan", "confidence": "low"},
                        {"n": 4, "speaker": None, "confidence": "low"}]})
    got = speakers.attribute(interview(), retry=False)
    # "Daniel Sheehan" is canonicalised to the description's fuller spelling -
    # the validator doing its job, not a mismatch.
    assert got["speakers"] == ["Jesse Michels", "Daniel Peter Sheehan"]
    assert [l and l["speaker"] for l in got["labels"]] == [
        "Jesse Michels", "Daniel Peter Sheehan", "Daniel Peter Sheehan", None]
    assert got["attributed"] == 3 and got["unattributed"] == 1


def test_a_null_speaker_stays_unattributed(replies):
    """"I can't tell" must survive as a gap. A guessed attribution is a claim
    about a real person that nothing downstream can audit."""
    replies({"speakers": ["A"],
             "labels": [{"n": 1, "speaker": None, "confidence": "low"}]})
    got = speakers.attribute(interview(1))
    assert got["labels"][0]["speaker"] is None and got["unattributed"] == 1


def test_out_of_range_numbering_is_discarded(replies):
    """A model that renumbers would otherwise attribute words to whoever happens
    to sit at that index."""
    replies({"speakers": ["A"], "labels": [
        {"n": 99, "speaker": "A", "confidence": "high"},
        {"n": 0, "speaker": "A", "confidence": "high"},
        {"n": 2, "speaker": "A", "confidence": "high"}]})
    got = speakers.attribute(interview(3))
    assert [l and l["speaker"] for l in got["labels"]] == [None, "A", None]


def test_batches_are_numbered_absolutely(replies):
    """Batch two must be numbered 3,4 — not restarted at 1, or its labels land
    on the first two paragraphs."""
    seen = replies({"speakers": [], "labels": []})
    speakers.attribute(interview(4), per_request=2)
    assert "[3]" in seen[1]["prompt"] and "[1]" not in seen[1]["prompt"]


def test_one_failed_batch_does_not_lose_the_others(replies):
    replies(LookupError("claude exited 1"),
            {"speakers": ["A"], "labels": [
                {"n": 3, "speaker": "A", "confidence": "high"},
                {"n": 4, "speaker": "A", "confidence": "high"}]})
    got = speakers.attribute(interview(4), per_request=2)
    assert [l and l["speaker"] for l in got["labels"]] == [None, None, "A", "A"]


def test_a_missing_cli_stops_immediately(replies):
    """It fails identically for every batch; grinding through 279 paragraphs to
    report the same thing wastes the user's time."""
    replies(speakers.NotAvailable("no claude"))
    with pytest.raises(speakers.NotAvailable):
        speakers.attribute(interview(4), per_request=2)


def test_garbled_output_is_not_a_crash(replies):
    replies("sorry, I can't do that")
    assert speakers.attribute(interview(2))["attributed"] == 0


# --------------------------------------------------------------------------
# folding labels in without touching the verbatim text
# --------------------------------------------------------------------------

def test_apply_never_edits_the_transcript_text():
    """ADR 0011: generated content lives beside the verbatim record, not in it."""
    t = interview(2)
    before = [p["text"] for p in t["paragraphs"]]
    speakers.apply(t, {"speakers": ["A", "B"], "model": "m", "labels": [
        {"speaker": "A", "confidence": "high"}, {"speaker": "B", "confidence": "low"}]})
    assert [p["text"] for p in t["paragraphs"]] == before
    assert t["paragraphs"][0]["speaker"] == "A"
    assert t["paragraphs"][1]["speaker_confidence"] == "low"


def test_apply_leaves_unattributed_paragraphs_alone():
    t = interview(2)
    speakers.apply(t, {"speakers": [], "model": "m",
                       "labels": [None, {"speaker": None, "confidence": "low"}]})
    assert "speaker" not in t["paragraphs"][0]
    assert "speaker" not in t["paragraphs"][1]


# --------------------------------------------------------------------------
# how it reads
# --------------------------------------------------------------------------

def test_markdown_names_the_speaker_at_each_change():
    t = doc(speakers=["Jesse Michels", "Daniel Sheehan"], speakers_by="claude",
            paragraphs=[
                {"start_ms": 0, "timestamp": "00:00", "turn": 0, "text": "a",
                 "speaker": "Jesse Michels", "speaker_confidence": "high"},
                {"start_ms": 1, "timestamp": "00:01", "turn": 0, "text": "b",
                 "speaker": "Daniel Sheehan", "speaker_confidence": "high"},
                {"start_ms": 2, "timestamp": "00:02", "turn": 0, "text": "c",
                 "speaker": "Daniel Sheehan", "speaker_confidence": "high"}])
    body = tk.to_markdown(t)
    assert "**Jesse Michels**: a" in body and "**Daniel Sheehan**: b" in body
    assert body.count("**Daniel Sheehan**") == 1  # only at the change


def test_markdown_marks_a_low_confidence_attribution():
    """An inferred label presented as certain is the failure mode to avoid."""
    t = doc(speakers=["A"], paragraphs=[
        {"start_ms": 0, "timestamp": "00:00", "turn": 0, "text": "x",
         "speaker": "A", "speaker_confidence": "low"}])
    assert "**A?**: x" in tk.to_markdown(t)


def test_frontmatter_says_the_attribution_is_generated():
    head = tk.to_markdown(doc(speakers=["A", "B"], speakers_by="claude"))
    assert "speakers: A, B" in head and "generated, not from the captions" in head


def test_unattributed_transcripts_keep_the_old_turn_markers():
    t = doc(turns=2, paragraphs=[
        {"start_ms": 0, "timestamp": "00:00", "turn": 0, "text": "a"},
        {"start_ms": 1, "timestamp": "00:01", "turn": 1, "text": "b"}])
    assert ">> b" in tk.to_markdown(t)


# --------------------------------------------------------------------------
# the description, which is free and was being discarded
# --------------------------------------------------------------------------

def test_the_opening_paragraph_is_taken_whole():
    """A paragraph, not a sentence — see the hook case below for why."""
    got = tk._first_sentence("Our incredible guest today is Nick Cook. "
                             "In the 1990s he edited Jane's Defence Weekly.\n\nLinks:")
    assert got.startswith("Our incredible guest today is Nick Cook.")
    assert "Jane's" in got


def test_a_description_that_opens_mid_hook_keeps_its_context():
    """The correction this function exists for. A talk on ontologies opens "A
    second refund on the same order." — one sentence of the failure story it is
    motivating. Alone that reads as a non-sequitur; in context it reads as an
    opening, and the paragraph goes on to name the speaker and his argument.
    """
    got = tk._first_sentence(
        "A second refund on the same order. A payout sent to the support desk. "
        "These are the mistakes a probabilistic agent makes. Frank Coyle argues "
        "most agent failures are ontological.\n\nSpeaker info:\n- https://x.com/x")
    assert "Frank Coyle argues" in got
    assert "x.com" not in got


@pytest.mark.parametrize("desc", [
    "https://example.com/a\nhttps://example.com/b",       # a link dump
    "- one\n- two\n- three",                              # a bullet list
    "00:00 Intro\n01:23 The middle\n04:56 The end",        # chapter timestamps
    "#ai #agents #ontology",                               # a hashtag tail
    "Speaker info:",                                       # a bare header
    "", "   ",
])
def test_furniture_is_not_mistaken_for_a_description(desc):
    """None of these say what the video is, so none of them belong in `about:`."""
    assert tk._first_sentence(desc) == ""


def test_furniture_is_skipped_to_reach_the_prose():
    got = tk._first_sentence("Subscribe here:\n- https://example.com\n\n"
                             "Nick Cook was the aviation editor of a defence journal "
                             "and later an aerospace consultant.")
    assert got.startswith("Nick Cook was the aviation editor")


def test_a_long_opening_is_truncated():
    got = tk._first_sentence("word " * 300)
    assert len(got) <= 303 and got.endswith("...")


def test_description_reaches_the_frontmatter():
    body = tk.to_markdown(doc(
        description="Our guest today is Nick Cook, once an aviation editor."))
    assert "about: Our guest today is Nick Cook, once an aviation editor." in body
