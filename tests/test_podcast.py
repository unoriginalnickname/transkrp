"""The podcast route: finding a feed, picking an episode, and saying it's ASR.

The feed here is the real one this path was built against, trimmed to three
items and with its quirks kept rather than tidied — a duration as a bare integer
on one item and H:MM:SS on another, a title with a double space in it, an
`itunes:` namespace declared alongside `podcast:`. Those quirks are the whole
reason feed parsing is not a one-liner, so a fixture that smooths them over
would test a feed nobody serves.

Whisper is stubbed everywhere. The real model is exercised by the live tests;
what these cover is everything around it, which is the part that decides whether
a document is trustworthy: that a published transcript wins over ASR, that the
timestamps resolve to the audio, and that a whisper document says so in its
frontmatter.
"""

import urllib.error

import pytest

from test_http import serve                                  # noqa: F401

import podcast
import transkrp


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>The Valued Cultures Podcast</title>
    <link>https://valuedcultures.podbean.com</link>
    <item>
      <title>Heather Hazen of Firaxis: Twin Legacies</title>
      <link>https://valuedcultures.podbean.com/e/heather/</link>
      <pubDate>Wed, 01 Jul 2026 14:18:21 -0300</pubDate>
      <description>&lt;p&gt;Civilization at 35.&lt;/p&gt;</description>
      <itunes:duration>2057</itunes:duration>
      <guid isPermaLink="false">valuedcultures.podbean.com/92f9496a</guid>
      <enclosure url="https://mcdn.podbean.com/mf/web/x/VC_Heather.mp3"
                 length="32924005" type="audio/mpeg"/>
    </item>
    <item>
      <title>Garrett Young of Empty Vessel:  Making Games</title>
      <link>https://valuedcultures.podbean.com/e/garrett/</link>
      <pubDate>Sun, 16 Mar 2026 09:00:00 -0300</pubDate>
      <description>Today we are joined by Garrett Young.</description>
      <itunes:duration>0:40:12</itunes:duration>
      <guid isPermaLink="false">valuedcultures.podbean.com/ad1ae4f6</guid>
      <enclosure url="https://mcdn.podbean.com/mf/web/y/VC_Garrett.mp3"
                 length="38000000" type="audio/mpeg"/>
    </item>
    <item>
      <title>An episode somebody wrote a transcript for</title>
      <link>https://valuedcultures.podbean.com/e/transcribed/</link>
      <pubDate>Tue, 10 Feb 2026 09:00:00 -0300</pubDate>
      <itunes:duration>37:00</itunes:duration>
      <guid isPermaLink="false">valuedcultures.podbean.com/deadbeef</guid>
      <enclosure url="https://mcdn.podbean.com/mf/web/z/VC_Other.mp3"
                 length="1000" type="audio/mpeg"/>
      <podcast:transcript url="https://example.invalid/ep.vtt"
                          type="text/vtt" language="en"/>
    </item>
  </channel>
</rss>
""".encode()


@pytest.fixture
def feed(monkeypatch):
    """Serve the fixture feed to any fetch, and record what was asked for."""
    asked = []

    def fake_get(url, timeout=podcast.TIMEOUT):
        asked.append(url)
        if url.endswith(".vtt"):
            return (b"WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n"
                    b"A real transcript, published by the show.\n")
        return FEED

    monkeypatch.setattr(podcast, "_get", fake_get)
    return asked


@pytest.fixture
def no_whisper(monkeypatch):
    """Fail loudly if a test reaches speech recognition without meaning to."""
    def boom(*a, **k):
        raise AssertionError("transcribe() should not have been called")
    monkeypatch.setattr(podcast, "transcribe", boom)
    return boom


def stub_whisper(monkeypatch, cues=None):
    cues = cues or [(0, 3000, "Hey everyone, welcome to the show."),
                    (3000, 7000, "Today we are joined by Garrett Young.")]
    monkeypatch.setattr(podcast, "transcribe",
                        lambda url, model=podcast.MODEL, progress=None:
                        (cues, f"faster-whisper {model} (int8, CPU)"))


# --- what counts as a podcast ------------------------------------------------

@pytest.mark.parametrize("target", [
    "The Valued Cultures Podcast",                       # a name, not a URL
    "https://feed.podbean.com/valuedcultures/feed.xml",  # a feed
    "https://example.com/rss",
    "https://podcasts.apple.com/us/podcast/x/id1735589599",
    "https://music.amazon.ca/podcasts/abc/episodes/def",
    "https://open.spotify.com/show/6Bjje",
    "podcast:https://feed.xml#abc123",
])
def test_recognised_as_a_podcast(target):
    assert podcast.is_podcast(target)


@pytest.mark.parametrize("target", [
    "https://www.youtube.com/watch?v=2SQXAPCdmPE",
    "https://youtu.be/2SQXAPCdmPE",
    "https://www.youtube.com/playlist?list=PLabc",
    "https://www.youtube.com/@channel",
])
def test_youtube_is_left_to_yt_dlp(target):
    """The dispatch must not steal a YouTube URL; everything else here is moot."""
    assert not podcast.is_podcast(target)


# --- feed parsing ------------------------------------------------------------

def test_parses_the_feed_into_episodes(feed):
    show = podcast.episodes("https://feed.invalid/f.xml")
    assert show["show"] == "The Valued Cultures Podcast"
    assert [e["title"] for e in show["episodes"]][:2] == [
        "Heather Hazen of Firaxis: Twin Legacies",
        "Garrett Young of Empty Vessel:  Making Games"]
    first = show["episodes"][0]
    assert first["audio"] == "https://mcdn.podbean.com/mf/web/x/VC_Heather.mp3"
    assert first["page"] == "https://valuedcultures.podbean.com/e/heather/"
    assert first["published"].startswith("Wed, 01 Jul 2026")


def test_html_in_a_description_is_not_carried_into_the_frontmatter(feed):
    """`&lt;p&gt;` arrives as markup and would land in a YAML `about:` line."""
    show = podcast.episodes("https://feed.invalid/f.xml")
    assert "<p>" not in show["episodes"][0]["description"]
    assert "Civilization at 35." in show["episodes"][0]["description"]


@pytest.mark.parametrize("value,seconds", [
    ("2057", 2057),         # bare integer, what podbean emits
    ("0:40:12", 2412),      # H:MM:SS
    ("37:00", 2220),        # MM:SS
    ("", 0),
    ("garbage", 0),
])
def test_duration_in_every_shape_a_host_emits(value, seconds):
    assert podcast._seconds(value) == seconds


def test_episode_ids_are_stable_across_runs(feed):
    """The id is in the filename, so --skip-existing depends on it not moving."""
    a = podcast.episodes("https://feed.invalid/f.xml")["episodes"]
    b = podcast.episodes("https://feed.invalid/f.xml")["episodes"]
    assert [e["id"] for e in a] == [e["id"] for e in b]
    assert len({e["id"] for e in a}) == 3       # and they distinguish episodes


def test_a_feed_that_is_not_a_feed_is_a_lookup_error(monkeypatch):
    monkeypatch.setattr(podcast, "_get", lambda url, timeout=30: b"<html>nope</html>")
    with pytest.raises(LookupError):
        podcast.episodes("https://example.invalid/not-a-feed")


def test_an_unreachable_feed_is_a_lookup_error(monkeypatch):
    def fail(url, timeout=30):
        raise urllib.error.URLError("no route to host")
    monkeypatch.setattr(podcast, "_get", fail)
    with pytest.raises(LookupError):
        podcast.episodes("https://example.invalid/f.xml")


# --- choosing an episode -----------------------------------------------------

def test_no_hint_takes_the_most_recent(feed):
    eps = podcast.episodes("https://feed.invalid/f.xml")["episodes"]
    assert podcast.pick(eps, None)["title"].startswith("Heather Hazen")


def test_a_partial_title_finds_the_episode(feed):
    eps = podcast.episodes("https://feed.invalid/f.xml")["episodes"]
    assert podcast.pick(eps, "Garrett Young")["title"].startswith("Garrett Young")


def test_matching_ignores_case(feed):
    eps = podcast.episodes("https://feed.invalid/f.xml")["episodes"]
    assert podcast.pick(eps, "garrett young")["title"].startswith("Garrett Young")


def test_a_hint_matching_nothing_says_what_was_closest(feed):
    eps = podcast.episodes("https://feed.invalid/f.xml")["episodes"]
    with pytest.raises(LookupError, match="Closest was"):
        podcast.pick(eps, "a completely unrelated conversation about bees")


# --- storefronts -------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://music.amazon.ca/podcasts/0d8e/episodes/ad1a",
    "https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk",
])
def test_a_storefront_link_explains_itself(url):
    """These pages are JavaScript shells: no metadata to scrape, audio DRM'd.

    The failure is unavoidable, so the only thing that matters is whether the
    message tells someone what to do instead.
    """
    with pytest.raises(LookupError) as e:
        podcast.feed_url(url)
    assert "storefront" in str(e.value)
    assert "name" in str(e.value)


def test_an_apple_link_is_resolved_by_id_not_by_searching(monkeypatch):
    """The id is in the URL, so there is no reason to fuzzy-match a show name."""
    seen = []

    def fake_json(url):
        seen.append(url)
        return {"results": [{"feedUrl": "https://feed.invalid/real.xml"}]}

    monkeypatch.setattr(podcast, "_json", fake_json)
    assert podcast.feed_url(
        "https://podcasts.apple.com/us/podcast/valued-cultures/id1735589599"
    ) == "https://feed.invalid/real.xml"
    assert "lookup?id=1735589599" in seen[0]
    assert "search" not in seen[0]


def test_a_bare_name_is_searched(monkeypatch):
    monkeypatch.setattr(podcast, "_json", lambda url: {
        "results": [{"collectionName": "The Valued Cultures Podcast",
                     "feedUrl": "https://feed.invalid/real.xml",
                     "trackCount": 21}]})
    assert podcast.feed_url("Valued Cultures") == "https://feed.invalid/real.xml"


def test_a_name_matching_no_show_is_a_lookup_error(monkeypatch):
    monkeypatch.setattr(podcast, "_json", lambda url: {"results": []})
    with pytest.raises(LookupError, match="no podcast found"):
        podcast.feed_url("asdkjhasdkjh not a real show")


# --- a published transcript beats ASR ----------------------------------------

def test_a_published_transcript_is_used_instead_of_whisper(feed, no_whisper):
    """Free, exact, and somebody meant it. `no_whisper` fails if ASR is reached."""
    t = podcast.transcript("https://feed.invalid/f.xml",
                           episode="somebody wrote a transcript")
    assert t["source"] == "published"
    assert "A real transcript, published by the show." in t["text"]
    assert not t["model"]


def test_an_unusable_published_transcript_falls_back_to_whisper(feed, monkeypatch):
    """A 404 on the transcript file must not lose the episode."""
    def fake_get(url, timeout=30):
        if url.endswith(".vtt"):
            raise urllib.error.HTTPError(url, 404, "gone", {}, None)
        return FEED
    monkeypatch.setattr(podcast, "_get", fake_get)
    stub_whisper(monkeypatch)
    t = podcast.transcript("https://feed.invalid/f.xml",
                           episode="somebody wrote a transcript")
    assert t["source"] == "whisper"


# --- the document -----------------------------------------------------------

def test_the_document_has_the_shape_every_other_document_has(feed, monkeypatch):
    """Downstream — markdown, the graph, attribution — must not need a branch."""
    stub_whisper(monkeypatch)
    t = podcast.transcript("https://feed.invalid/f.xml", episode="Garrett")
    for key in ("title", "video_id", "url", "source", "lang", "translated",
                "punctuated", "duration_ms", "paragraphs", "text", "channel",
                "upload_date", "chapters", "turns"):
        assert key in t, key
    assert t["paragraphs"][0]["timestamp"] == "00:00"
    assert isinstance(t["paragraphs"][0]["start_ms"], int)


def test_the_frontmatter_declares_that_this_is_asr(feed, monkeypatch):
    """The whole document is a guess. It has to say so, and say what made it."""
    stub_whisper(monkeypatch)
    md = transkrp.to_markdown(podcast.transcript("https://feed.invalid/f.xml",
                                                 episode="Garrett"))
    assert "source: whisper" in md
    assert "model: faster-whisper small (int8, CPU)" in md
    assert "names in particular should be checked" in md
    assert "audio: https://mcdn.podbean.com/mf/web/y/VC_Garrett.mp3" in md
    assert "feed: https://feed.invalid/f.xml" in md


def test_timestamps_link_into_the_audio(feed, monkeypatch):
    """Otherwise every citation in the file points at the top of the same hour."""
    # Long enough to clear MIN_WORDS, or the silence between them won't break a
    # paragraph and there is only one timestamp to check.
    stub_whisper(monkeypatch, cues=[
        (0, 3000, "So the thing about making games for that long is that you "
                  "learn where the time actually goes, and it is never where "
                  "you thought it was going to go at the start."),
        (65000, 70000, "Right, and that is the part nobody tells you early on.")])
    md = transkrp.to_markdown(podcast.transcript("https://feed.invalid/f.xml",
                                                 episode="Garrett"))
    assert "VC_Garrett.mp3#t=0)" in md
    assert "VC_Garrett.mp3#t=65)" in md


def test_a_youtube_document_still_anchors_to_youtube():
    """The audio anchor must not have changed the path it was built beside."""
    assert transkrp._at("https://www.youtube.com/watch?v=2SQXAPCdmPE&list=PL", 252000) \
        == "https://www.youtube.com/watch?v=2SQXAPCdmPE&t=252s"


def test_the_date_becomes_iso(feed, monkeypatch):
    stub_whisper(monkeypatch)
    t = podcast.transcript("https://feed.invalid/f.xml", episode="Garrett")
    assert t["upload_date"] == "2026-03-16"


# --- expansion and resume ----------------------------------------------------

def test_a_show_does_not_expand_to_every_episode_by_default(feed):
    """Each episode is a whisper run. Expanding 21 of them uninvited is a trap."""
    assert transkrp.expand("https://feed.invalid/f.xml") == \
        ["https://feed.invalid/f.xml"]


def test_playlist_takes_the_whole_feed(feed):
    urls = transkrp.expand("https://feed.invalid/f.xml", force_playlist=True)
    assert len(urls) == 3
    assert all(u.startswith(podcast.REF) for u in urls)


def test_an_expanded_reference_names_exactly_one_episode(feed, monkeypatch):
    """Round-trip: the id in the reference is the episode that gets fetched."""
    stub_whisper(monkeypatch)
    urls = transkrp.expand("https://feed.invalid/f.xml", force_playlist=True)
    t = podcast.transcript(urls[1])
    assert t["title"].startswith("Garrett Young")


def test_an_expanded_reference_does_not_expand_again(feed):
    ref = podcast.ref("https://feed.invalid/f.xml", "abc123")
    assert transkrp.expand(ref, force_playlist=True) == [ref]


def test_skip_existing_can_match_an_episode_on_its_filename(feed):
    """--skip-existing reads the id off the reference, as it does for a video."""
    urls = transkrp.expand("https://feed.invalid/f.xml", force_playlist=True)
    vid = transkrp.video_id(urls[0])
    assert vid and vid in transkrp.slug("Heather Hazen of Firaxis", vid)


def test_an_episode_dropped_from_the_feed_is_a_clean_error(feed):
    with pytest.raises(LookupError, match="no longer in"):
        podcast.transcript(podcast.ref("https://feed.invalid/f.xml", "gone404"))


# --- the dependency ----------------------------------------------------------

def test_a_missing_whisper_says_how_to_install_it(feed, monkeypatch):
    """The heavy dependency is optional, so its absence is a normal state."""
    import builtins
    real = builtins.__import__

    def no_faster_whisper(name, *a, **k):
        if name == "faster_whisper":
            raise ImportError("No module named 'faster_whisper'")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_faster_whisper)
    with pytest.raises(LookupError, match=r"\[podcast\]"):
        podcast.transcribe("https://example.invalid/ep.mp3")


def test_finding_a_feed_needs_no_speech_recognition(feed, no_whisper):
    """Browsing must work on a core install; only transcribing is expensive."""
    show = podcast.episodes("https://feed.invalid/f.xml")
    assert len(show["episodes"]) == 3
