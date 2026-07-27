"""Speaker attribution — offline. No API key, no network, no spend.

The Claude call is stubbed at the client. What's tested is our side: that names
come off the metadata rather than the mangled transcript, that a label the model
wouldn't commit to stays unattributed, that batch numbering can't smear labels
onto the wrong paragraphs, and that nothing bills without saying so first.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import speakers
import transkrp as tk
from test_transkrp import doc


# --------------------------------------------------------------------------
# a fake Claude
# --------------------------------------------------------------------------

class FakeMessage:
    def __init__(self, payload, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.content = [] if payload is None else [
            type("Block", (), {"type": "text", "text": json.dumps(payload)})()
        ]


class FakeClient:
    """Replays queued responses and records what it was asked."""

    def __init__(self, *payloads):
        self.queue, self.seen = list(payloads), []
        self.messages = self

    def create(self, **kwargs):
        self.seen.append(kwargs)
        out = self.queue.pop(0) if self.queue else {}
        if isinstance(out, Exception):
            raise out
        if isinstance(out, FakeMessage):
            return out
        return FakeMessage(out)


@pytest.fixture
def fake(monkeypatch):
    def install(*payloads):
        client = FakeClient(*payloads)
        monkeypatch.setattr(speakers, "_client", lambda: client)
        return client
    return install


def interview(n=4):
    return doc(
        title="The Man Involved In Every American Conspiracy",
        channel="Jesse Michels",
        upload_date="2024-08-22",
        description="Daniel Peter Sheehan is a Harvard trained constitutional "
                    "and public interest lawyer. " + "filler. " * 400,
        paragraphs=[{"start_ms": i * 1000, "timestamp": "00:00", "turn": 0,
                     "text": f"paragraph {i} words"} for i in range(n)],
    )


# --------------------------------------------------------------------------
# what the model is told
# --------------------------------------------------------------------------

def test_the_description_is_sent(fake):
    """The whole reason attribution can produce a *name*: ASR says "Danny shean",
    the description says "Daniel Peter Sheehan"."""
    client = fake({"speakers": [], "labels": []})
    speakers.attribute(interview())
    prompt = client.seen[0]["messages"][0]["content"]
    assert "Daniel Peter Sheehan" in prompt


def test_the_channel_is_offered_as_the_host(fake):
    client = fake({"speakers": [], "labels": []})
    speakers.attribute(interview())
    assert "Jesse Michels" in client.seen[0]["messages"][0]["content"]


def test_the_sponsor_tail_is_trimmed(fake):
    """Descriptions run to thousands of characters of links; the guest is named
    at the top, and the rest is input tokens nobody is buying anything with."""
    client = fake({"speakers": [], "labels": []})
    speakers.attribute(interview())
    assert len(client.seen[0]["messages"][0]["content"]) < 4000


def test_a_json_schema_is_enforced(fake):
    client = fake({"speakers": [], "labels": []})
    speakers.attribute(interview())
    fmt = client.seen[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["properties"]["labels"]["items"]["required"] == [
        "n", "speaker", "confidence"]


# --------------------------------------------------------------------------
# reading the answer back
# --------------------------------------------------------------------------

def test_labels_land_on_their_paragraphs(fake):
    fake({"speakers": ["Jesse Michels", "Daniel Sheehan"],
          "labels": [{"n": 1, "speaker": "Jesse Michels", "confidence": "high"},
                     {"n": 2, "speaker": "Daniel Sheehan", "confidence": "high"},
                     {"n": 3, "speaker": "Daniel Sheehan", "confidence": "low"},
                     {"n": 4, "speaker": None, "confidence": "low"}]})
    got = speakers.attribute(interview())
    assert got["speakers"] == ["Jesse Michels", "Daniel Sheehan"]
    assert [l and l["speaker"] for l in got["labels"]] == [
        "Jesse Michels", "Daniel Sheehan", "Daniel Sheehan", None]
    assert got["attributed"] == 3 and got["unattributed"] == 1


def test_a_null_speaker_stays_unattributed(fake):
    """"I can't tell" must survive as a gap. A guessed attribution is a claim
    about a real person that nothing downstream can audit."""
    fake({"speakers": ["A"], "labels": [{"n": 1, "speaker": None, "confidence": "low"}]})
    got = speakers.attribute(interview(1))
    assert got["labels"][0]["speaker"] is None
    assert got["unattributed"] == 1


def test_out_of_range_numbering_is_discarded(fake):
    """A model that renumbers would otherwise smear labels onto the wrong
    paragraphs — attributing words to whoever happens to sit at that index."""
    fake({"speakers": ["A"], "labels": [
        {"n": 99, "speaker": "A", "confidence": "high"},
        {"n": 0, "speaker": "A", "confidence": "high"},
        {"n": 2, "speaker": "A", "confidence": "high"}]})
    got = speakers.attribute(interview(3))
    assert [l and l["speaker"] for l in got["labels"]] == [None, "A", None]


def test_a_refusal_leaves_everything_unattributed(fake):
    """A refusal is a 200 with empty content, not an exception."""
    fake(FakeMessage(None, stop_reason="refusal"))
    got = speakers.attribute(interview(2))
    assert got["attributed"] == 0


def test_unparseable_output_is_not_a_crash(fake):
    client = FakeClient()
    client.queue = [FakeMessage(None)]
    client.queue[0].content = [type("B", (), {"type": "text", "text": "sorry, no"})()]
    speakers._client = lambda: client
    got = speakers.attribute(interview(2))
    assert got["attributed"] == 0


def test_batches_are_numbered_absolutely(fake):
    """Batch two must be numbered 3,4 — not restarted at 1, or its labels land
    on the first two paragraphs."""
    client = fake({"speakers": [], "labels": []}, {"speakers": [], "labels": []})
    speakers.attribute(interview(4), per_request=2)
    assert "[3]" in client.seen[1]["messages"][0]["content"]
    assert "[1]" not in client.seen[1]["messages"][0]["content"]


def test_one_failed_batch_does_not_lose_the_others(fake):
    fake(RuntimeError("transient"),
         {"speakers": ["A"], "labels": [{"n": 3, "speaker": "A", "confidence": "high"},
                                        {"n": 4, "speaker": "A", "confidence": "high"}]})
    got = speakers.attribute(interview(4), per_request=2)
    assert [l and l["speaker"] for l in got["labels"]] == [None, None, "A", "A"]


def test_an_auth_failure_stops_immediately(fake):
    """It will fail identically for all 279 paragraphs; grinding through them
    wastes the user's time and tells them nothing."""
    class AuthenticationError(Exception):
        pass

    fake(AuthenticationError("bad key"), {"speakers": [], "labels": []})
    with pytest.raises(AuthenticationError):
        speakers.attribute(interview(4), per_request=2)


# --------------------------------------------------------------------------
# cost, stated before it is spent
# --------------------------------------------------------------------------

def test_a_corpus_sized_estimate():
    """280,000 words - the real playlist. Cheap because output is labels, not
    text: a quarter-million words in, a few thousand short labels out."""
    usd = speakers.estimate_usd(280_000, "claude-opus-5")
    assert 1 < usd < 6


def test_cheaper_models_cost_less():
    big = speakers.estimate_usd(280_000, "claude-opus-5")
    small = speakers.estimate_usd(280_000, "claude-haiku-4-5")
    assert small < big / 3


def test_an_unknown_model_admits_it_rather_than_guessing():
    assert speakers.estimate_usd(1000, "some-future-model") is None


# --------------------------------------------------------------------------
# folding labels in without touching the verbatim text
# --------------------------------------------------------------------------

def test_apply_never_edits_the_transcript_text():
    """ADR 0011: generated content lives beside the verbatim record, never in it."""
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
    t = doc(speakers=["Jesse Michels", "Daniel Sheehan"], speakers_by="claude-opus-5",
            paragraphs=[
                {"start_ms": 0, "timestamp": "00:00", "turn": 0, "text": "a",
                 "speaker": "Jesse Michels", "speaker_confidence": "high"},
                {"start_ms": 1, "timestamp": "00:01", "turn": 0, "text": "b",
                 "speaker": "Daniel Sheehan", "speaker_confidence": "high"},
                {"start_ms": 2, "timestamp": "00:02", "turn": 0, "text": "c",
                 "speaker": "Daniel Sheehan", "speaker_confidence": "high"}])
    body = tk.to_markdown(t)
    assert "**Jesse Michels**: a" in body
    assert "**Daniel Sheehan**: b" in body
    assert body.count("**Daniel Sheehan**") == 1  # only at the change


def test_markdown_marks_a_low_confidence_attribution():
    """An inferred label presented as certain is the failure mode to avoid."""
    t = doc(speakers=["A"], paragraphs=[
        {"start_ms": 0, "timestamp": "00:00", "turn": 0, "text": "x",
         "speaker": "A", "speaker_confidence": "low"}])
    assert "**A?**: x" in tk.to_markdown(t)


def test_frontmatter_says_the_attribution_is_generated():
    t = doc(speakers=["A", "B"], speakers_by="claude-opus-5")
    head = tk.to_markdown(t)
    assert "speakers: A, B" in head
    assert "generated, not from the captions" in head


def test_unattributed_transcripts_keep_the_old_turn_markers():
    t = doc(turns=2, paragraphs=[
        {"start_ms": 0, "timestamp": "00:00", "turn": 0, "text": "a"},
        {"start_ms": 1, "timestamp": "00:01", "turn": 1, "text": "b"}])
    assert ">> b" in tk.to_markdown(t)


# --------------------------------------------------------------------------
# the description, which is free and was being discarded
# --------------------------------------------------------------------------

@pytest.mark.parametrize("desc,want", [
    ("Our incredible guest today is Nick Cook. In the 1990s...",
     "Our incredible guest today is Nick Cook."),
    ("Daniel Peter Sheehan is a lawyer\nSecond line", "Daniel Peter Sheehan is a lawyer"),
    ("", ""),
    ("   ", ""),
])
def test_first_sentence(desc, want):
    assert tk._first_sentence(desc) == want


def test_a_long_first_sentence_is_truncated():
    got = tk._first_sentence("word " * 200)
    assert len(got) < 240 and got.endswith("...")


def test_description_reaches_the_frontmatter():
    body = tk.to_markdown(doc(description="Our guest today is Nick Cook. More text."))
    assert "about: Our guest today is Nick Cook." in body
