"""Sponsor stripping — the API's quirks, and the guards on the cut.

`fetch` is tested over a real local socket rather than a mocked opener, for the
reason test_http.py gives: the behaviour that matters here is what `urllib` does
with a 404, and a mock is exactly the thing that can't tell us.
"""

import json

import pytest

from test_http import serve                                  # noqa: F401
from test_transkrp import ev, payload, stub_video, tk

import sponsors


@pytest.fixture
def api(monkeypatch):
    """Point the module at a local server that replays a queued script."""
    def use(srv):
        monkeypatch.setattr(sponsors, "API",
                            f"http://127.0.0.1:{srv.server_address[1]}")
        return srv
    return use


def body(*videos):
    """The prefix endpoint's shape: every video sharing the hash prefix."""
    return (200, json.dumps(list(videos)).encode(), None)


def entry(video_id, *segments, category="sponsor"):
    return {"videoID": video_id,
            "segments": [{"category": category, "actionType": "skip",
                          "segment": list(s), "votes": 0} for s in segments]}


def cue(start, end, text="words"):
    return (start, end, text)


# --------------------------------------------------------------------------
# what leaves the machine
# --------------------------------------------------------------------------

def test_the_video_id_is_never_sent():
    """The whole point of the prefix endpoint: the API learns four hex
    characters, not which video is being transcribed."""
    prefix = sponsors._prefix("D7_ipDqhtwk")
    assert len(prefix) == 4
    assert prefix not in "D7_ipDqhtwk" and "D7_ipDqhtwk" not in prefix


def test_other_videos_sharing_the_prefix_are_ignored(serve, api):
    """55 videos came back for one prefix when measured. 54 are not ours."""
    srv = api(serve(body(entry("someoneelse", (0, 30)),
                         entry("vid12345678", (10, 20)))))
    assert sponsors.fetch("vid12345678") == [(10_000, 20_000)]


# --------------------------------------------------------------------------
# a 404 means "nobody has submitted segments", not "something went wrong"
# --------------------------------------------------------------------------

def test_a_404_is_no_segments_not_an_error(serve, api):
    """Measured 2026-07-28: the API 404s both for a video with no submissions
    and for a nonsense ID. The two cannot be told apart, and the innocent case
    is by far the common one."""
    api(serve((404, b"Not Found", None)))
    assert sponsors.fetch("vid12345678") == []


def test_an_unreachable_api_is_no_segments(monkeypatch):
    """This must never be able to fail a fetch: the transcript is good without
    it. Port 1 on localhost refuses instantly."""
    monkeypatch.setattr(sponsors, "API", "http://127.0.0.1:1")
    assert sponsors.fetch("vid12345678") == []


def test_a_body_that_is_not_json_is_no_segments(serve, api):
    api(serve((200, b"<html>maintenance</html>", None)))
    assert sponsors.fetch("vid12345678") == []


def test_no_video_id_asks_nothing(monkeypatch):
    monkeypatch.setattr(sponsors, "API", "http://127.0.0.1:1")
    assert sponsors.fetch("") == []


# --------------------------------------------------------------------------
# reading the segments
# --------------------------------------------------------------------------

def test_seconds_become_milliseconds(serve, api):
    """The API speaks float seconds; everything in transkrp is integer ms."""
    api(serve(body(entry("vid12345678", (0, 13.055)))))
    assert sponsors.fetch("vid12345678") == [(0, 13_055)]


def test_only_the_sponsor_category_is_taken(serve, api):
    """selfpromo and the rest shade into content — a creator plugging their own
    project is usually discussing the video's subject."""
    api(serve(body(entry("vid12345678", (0, 10), category="selfpromo"))))
    assert sponsors.fetch("vid12345678") == []


@pytest.mark.parametrize("segment", [[5], [], "0,10", [10, 5], ["a", "b"], None])
def test_a_malformed_segment_is_skipped_not_fatal(serve, api, segment):
    """Crowd-sourced data, third-party service: assume nothing about the shape."""
    api(serve((200, json.dumps([{"videoID": "vid12345678", "segments": [
        {"category": "sponsor", "segment": segment}]}]).encode(), None)))
    assert sponsors.fetch("vid12345678") == []


# --------------------------------------------------------------------------
# the cut
# --------------------------------------------------------------------------

def test_a_cue_inside_the_span_goes():
    kept, applied = sponsors.strip(
        [cue(0, 2000, "sponsor read"), cue(5000, 7000, "the actual talk")],
        [(0, 3000)])
    assert [c[2] for c in kept] == ["the actual talk"]
    assert applied == [(0, 3000)]


def test_a_straddling_cue_goes_by_its_midpoint():
    """The cue that crosses the boundary needs an answer, and a midpoint is one
    without a fractional-overlap threshold to argue about."""
    mostly_sponsor = cue(2000, 4000)          # midpoint 3000, inside
    mostly_talk = cue(2900, 6000)             # midpoint 4450, outside
    kept, _ = sponsors.strip([mostly_sponsor, mostly_talk], [(0, 3500)])
    assert kept == [mostly_talk]


def test_a_span_that_removed_nothing_is_not_reported():
    """A span over a silent stretch cut nothing, and the document must not
    claim a gap that isn't there."""
    kept, applied = sponsors.strip([cue(5000, 6000)], [(60_000, 90_000)])
    assert kept == [cue(5000, 6000)] and applied == []


def test_a_span_eating_the_video_is_refused():
    """Crowd-sourced data is occasionally vandalised, and one bad segment can
    span a whole runtime. A transcript degraded to nothing is worse than a
    sponsor read left in."""
    segs = [cue(i * 1000, i * 1000 + 900) for i in range(10)]
    kept, applied = sponsors.strip(segs, [(0, 60_000)])
    assert kept == segs and applied == []


def test_nothing_to_strip_leaves_the_cues_alone():
    segs = [cue(0, 1000)]
    assert sponsors.strip(segs, []) == (segs, [])


# --------------------------------------------------------------------------
# through transcript() and into the document
# --------------------------------------------------------------------------

def test_the_default_never_contacts_the_api(monkeypatch):
    """Opt-in means opt-in: no flag, no second network dependency."""
    monkeypatch.setattr(sponsors, "fetch",
                        lambda *a, **k: pytest.fail("asked SponsorBlock unbidden"))
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")])
    tk.transcript("http://x")


def test_the_flag_cuts_the_cues_and_records_the_span(monkeypatch):
    monkeypatch.setattr(sponsors, "fetch", lambda *a, **k: [(0, 2000)])
    stub_video(monkeypatch, [ev(0, 2000, "Use code TRANSKRP."),
                             ev(5000, 2000, "Now, agents.")])
    t = tk.transcript("http://x", strip_sponsors=True)
    assert "TRANSKRP" not in t["text"] and "agents" in t["text"]
    assert t["sponsors_removed"] == [{"start_ms": 0, "end_ms": 2000}]


def test_a_document_that_cut_nothing_says_nothing(monkeypatch):
    """Absent means nothing was removed — not "you didn't ask"."""
    monkeypatch.setattr(sponsors, "fetch", lambda *a, **k: [])
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")])
    assert "sponsors_removed" not in tk.transcript("http://x", strip_sponsors=True)


def test_the_frontmatter_declares_the_gap(monkeypatch):
    """The part that makes removing content acceptable: a reader who finds a
    jump at 00:02 can see why, and refetch without the flag."""
    monkeypatch.setattr(sponsors, "fetch", lambda *a, **k: [(0, 2000)])
    stub_video(monkeypatch, [ev(0, 2000, "Use code TRANSKRP."),
                             ev(5000, 2000, "Now, agents.")])
    head = tk.to_markdown(tk.transcript("http://x", strip_sponsors=True))
    assert "sponsors_removed: 00:00-00:02" in head
    assert "SponsorBlock" in head
