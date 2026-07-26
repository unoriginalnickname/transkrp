"""Live tests: these really do hit YouTube.

Deselected by default. Run them with:

    python -m pytest tests/ -m network

The offline suite stubs the network, which means it will report every test
passing while the tool is completely broken — YouTube changing the json3 shape,
the timedtext endpoint starting to demand a PO token, yt-dlp's extraction going
stale. Those are the failures this project is actually likely to suffer, and
nothing offline can see them. So: a small number of assertions against real
videos, checking the things that would break *quietly*.

Loud breakage needs no test — you run the tool and it errors. What needs a test
is the transcript that comes back looking fine and is three times too long.

Environmental failures (rate limit, no network) skip rather than fail. A 429 is
not a regression, and a nightly run that cries wolf gets ignored.
"""

import re

import pytest

from test_transkrp import repetition, tk

pytestmark = pytest.mark.network


# Two videos chosen to still exist in five years.
ZOO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"       # the first YouTube video
LECTURE = "https://www.youtube.com/watch?v=Unzc731iCUY"   # MIT OCW, "How to Speak"
# The lecture is the useful one: ~1h, a manual track under a multi-track key
# (en-<trackid>, not "en"), and separate auto tracks — so it exercises track
# selection and gives auto-captions long enough for scroll-duplication to show.


def _fetch(*args, **kwargs):
    """Run a fetch, turning environmental failure into a skip."""
    try:
        return tk.transcript(*args, **kwargs)
    except LookupError as e:
        _skip_if_environmental(e)
        raise


def _skip_if_environmental(e):
    """Rate limits, dead network, and a fixture video that no longer exists.

    All three mean "we learned nothing", not "the code broke". A pulled video is
    the sneakiest: without this the suite fails with assertion errors that read
    exactly like a regression, and someone spends an hour on it.
    """
    msg = str(e).lower()
    for signal in ("rate-limited", "timed out", "429", "temporary failure",
                   "getaddrinfo", "connection", "unreachable"):
        if signal in msg:
            pytest.skip(f"environment, not a regression: {e}")
    for signal in ("unavailable", "private video", "removed", "terminated",
                   "does not exist", "no longer available"):
        if signal in msg:
            pytest.skip(f"fixture video is gone, not a regression - pick a new one: {e}")


@pytest.fixture(scope="session")
def zoo():
    return _fetch(ZOO)


@pytest.fixture(scope="session")
def lecture():
    return _fetch(LECTURE)


@pytest.fixture(scope="session")
def lecture_auto():
    """The auto-caption track — where scroll-duplication lives."""
    return _fetch(LECTURE, "en-orig")


@pytest.fixture(scope="session")
def lecture_info():
    try:
        return tk.probe(LECTURE)
    except LookupError as e:
        _skip_if_environmental(e)
        raise


# --------------------------------------------------------------------------
# the pipeline still runs
# --------------------------------------------------------------------------

def test_a_transcript_comes_back(zoo):
    assert zoo["video_id"] == "jNQXAC9IVRw"
    assert zoo["source"] == "manual"
    assert "elephants" in zoo["text"]


def test_the_long_one_comes_back_whole(lecture):
    """A truncated fetch would still parse; it would just be short."""
    assert 7_000 < len(lecture["text"].split()) < 12_000
    assert lecture["duration_ms"] > 3_000_000  # a real hour-long lecture


def test_the_multi_track_key_is_still_found(lecture_info):
    """This video's manual track is keyed en-<trackid>, not "en".

    If track selection regresses to a plain "en" lookup it falls through to
    auto-captions here — quietly, with worse text.
    """
    source, key, _ = tk.pick_track(lecture_info)
    assert source == "manual"
    assert key.startswith("en-") and len(key) > 5


def test_manual_is_still_preferred_over_auto(lecture, lecture_auto):
    assert lecture["source"] == "manual"
    assert lecture_auto["source"] == "auto"


# --------------------------------------------------------------------------
# the quiet ones
# --------------------------------------------------------------------------

def test_auto_captions_are_not_duplicated(lecture_auto):
    """A canary for json3 starting to duplicate the way .vtt does.

    Note what this does *not* test. Disabling our aAppend filter leaves this
    passing, because that filter is inert — json3 never emits the repeated text,
    so there is nothing for it to remove (see ADR 0002's correction). The guard
    here is against YouTube changing that: if a future format re-serialises the
    scrolling box, the transcript comes back looking perfectly normal and three
    times too long, and nothing else in either suite would notice.

    The detector itself is verified offline, in test_repetition_detects_*.
    """
    assert repetition(lecture_auto["text"]) < 0.20


def test_the_auto_track_is_not_wildly_longer_than_the_manual_one(lecture, lecture_auto):
    """Same hour of speech, so the word counts should be close.

    Undetected scroll-duplication would put this at roughly 3x.
    """
    ratio = len(lecture_auto["text"].split()) / len(lecture["text"].split())
    assert 0.7 < ratio < 1.4


def test_captions_arrive_as_json3_not_an_empty_body(lecture_info):
    """The PO-token failure returns HTTP 200 with nothing in it.

    Checked at the fetch rather than through transcript(), because this is about
    what came off the wire.
    """
    fmts = lecture_info["automatic_captions"]["en"]
    url = next(f["url"] for f in fmts if f["ext"] == "json3")
    body = tk._get(url)
    assert body.strip(), "empty body - the endpoint wants a PO token"
    assert b'"events"' in body, "not json3 any more"


def test_timestamps_are_ordered_and_within_the_video(lecture):
    """Out-of-order anchors would make every citation in the file wrong."""
    starts = [p["start_ms"] for p in lecture["paragraphs"]]
    assert starts == sorted(starts)
    assert starts[-1] <= lecture["duration_ms"]


def test_punctuation_is_still_detected(lecture):
    """A regression here reads as 160-word walls, not as an error."""
    assert lecture["punctuated"] is True
    assert re.search(r"[.!?]", lecture["paragraphs"][0]["text"])


def test_typography_is_normalised(lecture):
    """Non-breaking hyphens and spaces look right and don't match a search."""
    assert "‑" not in lecture["text"]
    assert " " not in lecture["text"]


# --------------------------------------------------------------------------
# discovery and failure
# --------------------------------------------------------------------------

def test_a_playlist_still_expands():
    try:
        urls = tk.expand("https://www.youtube.com/playlist?list=PLZHQObOWTQDNU6R1_67000Dx_ZCJB-3pi")
    except LookupError as e:
        _skip_if_environmental(e)
        raise
    assert len(urls) > 1
    assert all(re.search(r"[\w-]{11}", u) for u in urls)


def test_an_unavailable_video_is_a_clean_error():
    """Not a yt-dlp traceback."""
    with pytest.raises(LookupError) as caught:
        tk.probe("https://www.youtube.com/watch?v=00000000000")
    assert "unavailable" in str(caught.value).lower()
