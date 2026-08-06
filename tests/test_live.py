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
HEARING = "https://www.youtube.com/watch?v=QVdD66_ej8g"   # multi-speaker, 474 '>>'
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


@pytest.fixture(scope="session")
def hearing_info():
    """A multi-speaker video, for the one thing a lecture can't show.

    The lecture is one man talking for an hour, so it carries no '>>' at all.
    Only a genuine multi-speaker recording says anything about the marker
    convention.
    """
    try:
        return tk.probe(HEARING)
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

# --------------------------------------------------------------------------
# canaries for the measurements this design rests on
#
# Each of these was established by measuring once, during the research, and then
# written into an ADR or a comment as though it were permanent. It isn't — every
# one is a fact about YouTube that YouTube can change. These fail if it does.
# --------------------------------------------------------------------------

def test_speaker_markers_are_still_cue_leading(hearing_info):
    """`>>` at the start of a cue, never mid-cue — 474 of 474 when measured.

    _split_turns has a branch for a mid-cue marker that consequently never fires
    (documented in its docstring). If YouTube changes the convention, that branch
    starts carrying real traffic and this is how we find out.

    Needs a multi-speaker video: the lecture is one person for an hour and has no
    markers at all.
    """
    segs = tk.segments(hearing_info, "auto", "en")
    marked = [t for _, _, t in segs if ">>" in t]
    if len(marked) < 5:
        pytest.skip("this video has too few speaker markers to say anything")
    leading = [t for t in marked if t.lstrip().startswith(">>")]
    assert len(leading) == len(marked), f"{len(marked) - len(leading)} mid-cue markers"


def test_caption_urls_are_still_signed_and_ip_bound(lecture_info):
    """ADR 0003 rests on this: the URL carries ip/expire/signature.

    It is why the fetch has to happen on the machine that did the extraction, why
    a 403 is reported as "expired or issued for a different IP", and why the
    transcript dict must not be treated as re-fetchable later.
    """
    import urllib.parse
    fmts = lecture_info["automatic_captions"]["en"]
    url = next(f["url"] for f in fmts if f["ext"] == "json3")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert {"ip", "expire", "signature", "sparams"} <= set(q)


def test_youtube_still_offers_a_wall_of_machine_translations(lecture_info):
    """Track selection avoids these deliberately; ~150 of them exist.

    A German video offering an "en" auto track is why the ordering prefers the
    original ASR over a translation. If the wall ever disappears, that ordering
    is solving a problem that no longer exists.
    """
    auto = lecture_info.get("automatic_captions") or {}
    assert len(auto) > 50, f"only {len(auto)} auto tracks - has this changed?"
    assert "de" in auto and "ja" in auto  # translations of an English lecture


def test_json3_is_still_dramatically_shorter_than_the_vtt(lecture_auto, lecture_info):
    """The measurement the whole json3 choice rests on: 2.9x, freshly checked.

    ADR 0002 says .vtt re-serialises the scrolling box and json3 doesn't. This
    downloads both and compares. If the gap closes, the format choice is no
    longer load-bearing and the ADR needs revisiting.
    """
    import re
    fmts = lecture_info["automatic_captions"]["en-orig"]
    vtt_url = next((f["url"] for f in fmts if f["ext"] == "vtt"), None)
    if not vtt_url:
        pytest.skip("no vtt track offered to compare against")
    try:
        raw = tk._get(vtt_url).decode("utf-8", "replace")
    except LookupError as e:
        _skip_if_environmental(e)
        raise
    # Strip it the naive way, which is the thing being compared against.
    naive = re.sub(r"<[^>]+>|^(WEBVTT|Kind:|Language:|\d\d:\d\d:.*)$", "",
                   raw, flags=re.M).split()
    ours = lecture_auto["text"].split()
    assert len(naive) > len(ours) * 2, (
        f"vtt {len(naive)} vs json3 {len(ours)} - the duplication gap has closed")


def test_a_foreign_video_prefers_its_own_language(lecture_info):
    """The bug a synthetic fixture missed: an "en" auto track exists for every
    video, so preferring English gave a machine translation of a machine
    transcription while the original sat one line below.

    Simulated on real track data rather than a hand-made dict: pretend this
    lecture is German and has no human transcript, so the only choice left is
    between the original-language ASR and the English translation of it.

    (A *manual* English track would rightly still win — a human translation beats
    a machine one — which is why the manual tracks are dropped here.)
    """
    pretend = dict(lecture_info, language="de", subtitles={})
    if "de" not in (pretend.get("automatic_captions") or {}):
        pytest.skip("no German track on this video to prefer")
    source, key, translated = tk.pick_track(pretend)
    assert key.startswith("de"), f"picked {key!r} over the spoken language"
    assert translated is False


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


# --- podcasts ---------------------------------------------------------------
#
# The same argument as above, for a different set of undocumented endpoints. The
# iTunes directory has no contract with us, feed hosts change their markup, and a
# show can move hosts entirely. None of that is visible offline, where the feed
# is a fixture.
#
# Whisper itself is deliberately not run here: it is minutes of CPU per episode
# and it is the one part that isn't an undocumented remote API. What these check
# is everything up to it.

SHOW = "The Valued Cultures Podcast"


def test_a_show_name_resolves_to_its_feed():
    """The iTunes Search API, which is the entry point for every podcast run."""
    import podcast
    try:
        feed = podcast.feed_url(SHOW)
    except LookupError as e:
        pytest.skip(f"directory unreachable: {e}")
    assert feed.startswith("http")
    assert "podbean.com" in feed or ".xml" in feed or "rss" in feed


def test_a_real_feed_still_parses_into_episodes():
    """Feed markup is somebody else's, and it changes without telling us."""
    import podcast
    try:
        show = podcast.episodes(podcast.feed_url(SHOW))
    except LookupError as e:
        pytest.skip(f"feed unreachable: {e}")
    assert show["show"]
    assert show["episodes"], "feed parsed but yielded no episodes"
    playable = [e for e in show["episodes"] if e["audio"]]
    assert playable, "no episode had an <enclosure> to transcribe"
    ep = playable[0]
    assert ep["audio"].startswith("http")
    assert ep["id"] and len(ep["id"]) == 11
    assert ep["duration_s"] > 0, "no episode duration parsed off the feed"


def test_an_apple_link_resolves_by_id():
    """The lookup path, which avoids fuzzy-matching a show name to the wrong show."""
    import podcast
    try:
        feed = podcast.feed_url(
            "https://podcasts.apple.com/us/podcast/valued-cultures/id1735589599")
    except LookupError as e:
        pytest.skip(f"directory unreachable: {e}")
    assert "podbean.com" in feed


def test_the_episode_audio_is_really_fetchable():
    """The enclosure being public and undrmed is the assumption the route rests on."""
    import urllib.request
    import podcast
    try:
        show = podcast.episodes(podcast.feed_url(SHOW))
    except LookupError as e:
        pytest.skip(f"feed unreachable: {e}")
    audio = next(e["audio"] for e in show["episodes"] if e["audio"])
    req = urllib.request.Request(audio, headers={"User-Agent": podcast.UA})
    # Range: one frame, not forty megabytes. Enough to prove it serves audio.
    req.add_header("Range", "bytes=0-2047")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type", "")
    except OSError as e:
        pytest.skip(f"audio host unreachable: {e}")
    assert body, "enclosure served no bytes"
    assert "audio" in ctype or "octet-stream" in ctype, ctype
