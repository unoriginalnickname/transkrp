"""Offline tests: nothing here touches the network.

The parts worth testing are the ones that read a real caption feed wrong in ways
you don't notice — scroll-duplicates that silently triple the word count, a
paragraph break that strands two words, a 200 response with an empty body. So
the fixtures are shaped like real json3 payloads, and the network is stubbed at
the one function that performs it.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transkrp as tk


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def ev(start, dur, *texts, append=False):
    """One json3 event. `append=True` marks a scroll-repeat."""
    e = {"tStartMs": start, "dDurationMs": dur, "segs": [{"utf8": t} for t in texts]}
    if append:
        e["aAppend"] = 1
    return e


def payload(*events):
    return json.dumps({"events": list(events)}).encode("utf-8")


def track(info_key, key, body, monkeypatch):
    """An info dict whose one json3 track serves `body`."""
    monkeypatch.setattr(tk, "_get", lambda url, tries=4, proxy=None: body)
    return {info_key: {key: [{"ext": "vtt", "url": "http://v"},
                             {"ext": "json3", "url": "http://j"}]}}


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(tk.time, "sleep", slept.append)
    return slept


# --------------------------------------------------------------------------
# segments: the json3 read
# --------------------------------------------------------------------------

def test_scroll_duplicates_are_dropped(monkeypatch):
    """The bug that triples a transcript.

    Auto-captions repeat emitted text as the two-line box scrolls, flagged
    aAppend. Counting them is how a naive .vtt strip reports 14k words for a 5k
    word talk.
    """
    info = track("subtitles", "en", payload(
        ev(0, 1000, "hello there"),
        ev(1000, 10, "\n", append=True),
        ev(1010, 1000, "hello there general kenobi"),  # the scroll re-emit
    ), monkeypatch)
    segs = tk.segments(info, "manual", "en")
    assert [s[2] for s in segs] == ["hello there", "hello there general kenobi"]


def test_an_append_event_carrying_words_is_still_dropped(monkeypatch):
    """Proves the aAppend guard does something, since real data never asks it to.

    Measured across three videos: every aAppend event holds exactly "\\n", so the
    `if text` check downstream would drop them anyway and disabling this filter
    changes no output at all (ADR 0002's correction). That makes the guard
    untested by accident — real feeds cannot exercise it. This constructs the
    case it exists for: an append that does carry text, which is a continuation
    of what is already on screen and would duplicate silently.
    """
    info = track("automatic_captions", "en", payload(
        ev(0, 1000, "the quick brown fox"),
        ev(1000, 1000, "the quick brown fox jumps", append=True),
    ), monkeypatch)
    assert [s[2] for s in tk.segments(info, "auto", "en")] == ["the quick brown fox"]


def test_events_without_segs_are_skipped(monkeypatch):
    info = track("subtitles", "en", payload(
        {"tStartMs": 0},                      # window definition, no text
        ev(10, 100, "", "  "),                # whitespace only
        ev(200, 100, "real"),
    ), monkeypatch)
    assert [s[2] for s in tk.segments(info, "manual", "en")] == ["real"]


def test_segment_timing_and_multi_seg_join(monkeypatch):
    info = track("automatic_captions", "en", payload(ev(1500, 700, "one ", "two")),
                 monkeypatch)
    assert tk.segments(info, "auto", "en") == [(1500, 2200, "one two")]


def test_missing_duration_is_not_a_crash(monkeypatch):
    """dDurationMs is absent on the last event of some tracks."""
    info = track("subtitles", "en",
                 json.dumps({"events": [{"tStartMs": 5, "segs": [{"utf8": "x"}]}]}).encode(),
                 monkeypatch)
    assert tk.segments(info, "manual", "en") == [(5, 5, "x")]


def test_empty_body_names_the_po_token(monkeypatch):
    """The live failure mode: HTTP 200, empty body, when a PO token is needed.

    Unguarded this dies on "Expecting value: line 1 column 1", which tells the
    user nothing.
    """
    info = track("subtitles", "en", b"", monkeypatch)
    with pytest.raises(LookupError, match="PO token"):
        tk.segments(info, "manual", "en")


def test_truncated_body_is_reported_cleanly(monkeypatch):
    info = track("subtitles", "en", b'{"events": [{"tStartMs"', monkeypatch)
    with pytest.raises(LookupError, match="not valid json3"):
        tk.segments(info, "manual", "en")


def test_events_key_absent(monkeypatch):
    info = track("subtitles", "en", b'{"wireMagic": "pb3"}', monkeypatch)
    assert tk.segments(info, "manual", "en") == []


def test_track_without_json3_format(monkeypatch):
    info = {"subtitles": {"en": [{"ext": "vtt", "url": "http://v"}]}}
    with pytest.raises(LookupError, match="no json3"):
        tk.segments(info, "manual", "en")


# --------------------------------------------------------------------------
# _clean
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# the duplication detector the live canary relies on
# --------------------------------------------------------------------------

PROSE = ("the uniform code of military justice specifies court martial for any "
         "officer who sends a soldier into battle without a weapon there ought "
         "to be a similar protection for students because students should not go "
         "out into life without the ability to communicate")


def repetition(text, n=3):
    """Fraction of n-grams that repeat an earlier one.

    Lives here, in the offline suite, because this is where it can be verified —
    tests/test_live.py imports it to use as a canary against real captions.
    """
    words = text.lower().split()
    grams = [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]
    return 1 - len(set(grams)) / len(grams) if grams else 0.0


def scrolled(text, times=3):
    """Re-serialise text the way a .vtt scroll does: each phrase, repeatedly."""
    words = text.split()
    out = []
    for i in range(0, len(words), 4):
        for _ in range(times):
            out.extend(words[i:i + 4])
    return " ".join(out)


def test_repetition_detects_scroll_duplication():
    """The live canary asserts repetition() stays below 0.20. Prove it moves."""
    assert repetition(PROSE) < 0.20
    assert repetition(scrolled(PROSE)) > 0.20


def test_repetition_tolerates_a_verbal_tic():
    """A speaker with a catchphrase must not trip the canary."""
    tic = " ".join(f"{w} you know" if i % 7 == 0 else w
                   for i, w in enumerate(PROSE.split()))
    assert repetition(tic) < 0.20


def test_repetition_of_too_little_text():
    assert repetition("one two") == 0.0


def test_clean_normalises_lookalike_typography():
    """U+2011 and U+00A0 read as a hyphen and a space but don't match one."""
    assert tk._clean("real‑time systems​") == "real-time systems"


def test_clean_collapses_caption_box_newlines():
    assert tk._clean("a line\nbroken by the box") == "a line broken by the box"


# --------------------------------------------------------------------------
# punctuation detection
# --------------------------------------------------------------------------

def segs(*texts, step=1000):
    return [(i * step, i * step + step, t) for i, t in enumerate(texts)]


def test_punctuation_is_detected_not_assumed():
    assert tk.is_punctuated(segs("Hello there.", "How are you?", "Fine.")) is True
    assert tk.is_punctuated(segs("hello there", "how are you", "fine")) is False


def test_punctuation_survives_trailing_quotes_and_brackets():
    assert tk.is_punctuated(segs('he said "go."', 'and (that was that.)')) is True


def test_punctuation_threshold_is_lenient():
    """One sentence end in twenty is enough: cues break mid-sentence."""
    assert tk.is_punctuated(segs(*(["no ending here"] * 19 + ["but here."]))) is True


def test_punctuation_of_empty_track():
    assert tk.is_punctuated([]) is False


# --------------------------------------------------------------------------
# speaker turns
# --------------------------------------------------------------------------

def test_leading_marker_starts_a_turn():
    out = tk._split_turns([(0, 1, ">> hello")])
    assert out == [(0, 1, "hello", True)]


def test_marker_mid_segment_splits_it():
    out = tk._split_turns([(0, 1, "bye now >> hello there")])
    assert out == [(0, 1, "bye now", False), (0, 1, "hello there", True)]


def test_unmarked_segment_continues_the_turn():
    assert tk._split_turns([(0, 1, "just talking")]) == [(0, 1, "just talking", False)]


def test_multiple_chevrons_are_one_marker():
    assert tk._split_turns([(0, 1, ">>> hi")]) == [(0, 1, "hi", True)]


# --------------------------------------------------------------------------
# paragraphing
# --------------------------------------------------------------------------

def test_speaker_change_always_breaks_even_a_short_turn():
    """A one-word answer is its own paragraph; merging it loses the exchange."""
    paras = tk.paragraphs(segs(">> Do you agree?", ">> Yes.", ">> Why?"),
                          punctuated=True)
    assert [p[2] for p in paras] == ["Do you agree?", "Yes.", "Why?"]
    assert [p[1] for p in paras] == [1, 2, 3]


def test_long_silence_breaks_a_paragraph():
    long = "word " * 30
    s = [(0, 2000, long.strip()), (9000, 11000, "after the pause")]
    assert len(tk.paragraphs(s, punctuated=False)) == 2


def test_short_utterance_is_not_stranded_by_a_pause():
    """Below MIN_WORDS the gap is ignored, or every "Right." becomes a para."""
    s = [(0, 1000, "right"), (9000, 10000, "so as I was saying")]
    assert len(tk.paragraphs(s, punctuated=False)) == 1


def test_unpunctuated_text_is_capped_at_the_target():
    s = [(i * 500, i * 500 + 500, "word " * 10) for i in range(30)]
    paras = tk.paragraphs(s, punctuated=False, target=50)
    assert all(len(p[2].split()) <= 60 for p in paras)
    assert len(paras) > 1


def test_punctuated_text_runs_past_the_target_to_a_sentence_end():
    """The target is where we start looking for a break, not where we cut."""
    s = [(i * 500, i * 500 + 500, "word " * 10) for i in range(3)]  # 30 words
    s.append((1500, 2000, "so that is the end."))
    paras = tk.paragraphs(s, punctuated=True, target=30)
    assert paras[0][2].endswith("end.")
    assert len(paras) == 1


def test_punctuated_text_is_still_capped_when_no_sentence_ends():
    """An unpunctuated stretch inside a punctuated track can't run forever."""
    s = [(i * 500, i * 500 + 500, "word " * 20) for i in range(10)]
    paras = tk.paragraphs(s, punctuated=True, target=30)
    assert len(paras) > 1
    assert all(len(p[2].split()) <= 80 for p in paras)


def test_target_below_the_minimum_is_clamped():
    """A 1-word target would emit a paragraph per cue."""
    s = [(i * 500, i * 500 + 500, "word " * 10) for i in range(10)]
    assert len(tk.paragraphs(s, punctuated=False, target=1)) < 10


def test_sound_tag_only_paragraph_is_dropped():
    """"[Music]" alone is not a turn; inline it would be worth keeping."""
    s = [(0, 1000, "[Music]"), (30000, 31000, "and we are back " * 10)]
    paras = tk.paragraphs(s, punctuated=False)
    assert not any(p[2] == "[Music]" for p in paras)


def test_paragraph_start_is_its_first_cue():
    s = [(4000, 5000, "first"), (5000, 6000, "second")]
    assert tk.paragraphs(s, punctuated=False)[0][0] == 4000


def test_no_segments_no_paragraphs():
    assert tk.paragraphs([], punctuated=False) == []


# --------------------------------------------------------------------------
# track selection
# --------------------------------------------------------------------------

def info_with(manual=(), auto=(), language="en"):
    return {"subtitles": {k: [] for k in manual},
            "automatic_captions": {k: [] for k in auto},
            "language": language}


def test_manual_beats_auto():
    assert tk.pick_track(info_with(manual=["en"], auto=["en"])) == ("manual", "en", False)


def test_plain_en_beats_a_regional_variant():
    src, key, _ = tk.pick_track(info_with(manual=["en-GB", "en", "en-US"]))
    assert key == "en"


def test_multi_track_video_falls_back_to_the_odd_key():
    """A video with several English tracks exposes en-<trackid> and no "en"."""
    src, key, _ = tk.pick_track(info_with(manual=["en-j3PyPqV-e1s"], auto=["en"]))
    assert (src, key) == ("manual", "en-j3PyPqV-e1s")


def test_non_english_video_flags_a_machine_translation():
    src, key, translated = tk.pick_track(info_with(auto=["en"], language="ja"))
    assert (src, key, translated) == ("auto", "en", True)


def test_manual_track_is_never_flagged_translated():
    _, _, translated = tk.pick_track(info_with(manual=["en"], language="ja"))
    assert translated is False


def test_requested_track_wins():
    assert tk.pick_track(info_with(manual=["en"], auto=["en-orig"]), "en-orig")[1] == "en-orig"


def test_missing_requested_track_lists_what_exists():
    with pytest.raises(LookupError, match="de, fr"):
        tk.pick_track(info_with(manual=["de", "fr"]), "en")


def test_foreign_video_falls_back_to_its_own_language():
    """A German video with a German track shouldn't demand --lang to say so."""
    assert tk.pick_track(info_with(manual=["de"], language="de")) == ("manual", "de", False)


def test_english_still_wins_when_both_exist():
    """The fallback is a fallback: it must not change what English videos do."""
    src, key, _ = tk.pick_track(info_with(manual=["de", "en"], language="de"))
    assert key == "en"


def test_native_fallback_prefers_manual_over_auto():
    src, key, tr = tk.pick_track(info_with(manual=["ja"], auto=["ja"], language="ja"))
    assert (src, key, tr) == ("manual", "ja", False)


def test_native_auto_track_is_not_called_a_translation():
    """Japanese ASR on a Japanese video is one lossy step, not two."""
    assert tk.pick_track(info_with(auto=["ja"], language="ja")) == ("auto", "ja", False)


def test_a_human_native_track_beats_machine_translated_english():
    """Caught live on a German news video, not by any synthetic fixture.

    YouTube lists ~150 machine translations of its ASR beside the original, so a
    German video offers an "en" auto track. Preferring it gave a machine
    translation of a machine transcription while the human German transcript sat
    one line below.
    """
    src, key, tr = tk.pick_track(info_with(manual=["de"], auto=["en", "de"], language="de"))
    assert (src, key, tr) == ("manual", "de", False)


def test_original_asr_beats_a_machine_translation():
    """With no manual track at all, the original ASR still beats a translation."""
    src, key, tr = tk.pick_track(info_with(auto=["en", "ja"], language="ja"))
    assert (src, key, tr) == ("auto", "ja", False)


def test_a_human_english_translation_is_still_preferred():
    """A manual "en" track is a real translation by a person, not a machine."""
    src, key, _ = tk.pick_track(info_with(manual=["en", "de"], auto=["de"], language="de"))
    assert (src, key) == ("manual", "en")


def test_machine_translation_is_the_last_resort():
    """Nothing manual and no original ASR: take the translation, flagged."""
    src, key, tr = tk.pick_track(info_with(auto=["en"], language="ja"))
    assert (src, key, tr) == ("auto", "en", True)


def test_lang_auto_skips_the_english_preference():
    src, key, _ = tk.pick_track(info_with(manual=["de", "en"], language="de"), "auto")
    assert key == "de"


def test_lang_auto_on_an_english_video_is_still_english():
    assert tk.pick_track(info_with(manual=["en"], language="en"), "auto")[1] == "en"


def test_error_when_neither_english_nor_the_spoken_language_exists():
    with pytest.raises(LookupError, match=r"English or ja.*--lang"):
        tk.pick_track(info_with(manual=["de", "fr"], language="ja"))


def test_regional_native_key_matches_the_bare_language():
    """language is "de-DE" but the track is keyed "de"."""
    assert tk.pick_track(info_with(manual=["de"], language="de-DE"))[1] == "de"


def test_no_captions_at_all_says_so():
    with pytest.raises(LookupError, match="no captions at all"):
        tk.pick_track(info_with())


def test_track_list_is_truncated():
    assert "+2 more" in tk._names({k: [] for k in "abcdefghij"})


# --------------------------------------------------------------------------
# retry and network handling
# --------------------------------------------------------------------------

import urllib.error


class FakeOpener:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0
        self.addheaders = []

    def open(self, url, timeout=None):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return _Body(out)


class _Body:
    def __init__(self, data): self.data = data
    def read(self): return self.data
    def __enter__(self): return self
    def __exit__(self, *a): return False


def http(code):
    return urllib.error.HTTPError("http://j", code, "boom", {}, None)


def test_rate_limit_is_retried_then_succeeds(monkeypatch, no_sleep):
    op = FakeOpener(http(429), http(429), b"ok")
    monkeypatch.setattr(tk, "_opener", lambda proxy=None: op)
    assert tk._get("http://j") == b"ok"
    assert op.calls == 3
    assert len(no_sleep) == 2


@pytest.mark.parametrize("value,want", [
    ("30", 30.0),
    (" 5 ", 5.0),
    ("3600", float(tk.MAX_RETRY_AFTER)),   # capped, not obeyed
    ("0", None),                           # "come back immediately" is not a wait
    ("-5", None),
    ("Wed, 21 Oct 2026 07:28:00 GMT", None),  # HTTP-date form, deliberately ignored
    (None, None),                          # header absent
])
def test_retry_after_parsing(value, want):
    headers = {"Retry-After": value} if value is not None else {}
    err = urllib.error.HTTPError("http://j", 429, "slow down", headers, None)
    assert tk._retry_after(err) == want


def test_backoff_grows(no_sleep):
    assert tk._backoff(0) < tk._backoff(2) < tk._backoff(4)


def test_persistent_rate_limit_suggests_the_proxy(monkeypatch, no_sleep):
    monkeypatch.setattr(tk, "_opener", lambda proxy=None: FakeOpener(*[http(429)] * 4))
    with pytest.raises(LookupError, match="proxy"):
        tk._get("http://j")


def test_expired_url_is_explained(monkeypatch, no_sleep):
    monkeypatch.setattr(tk, "_opener", lambda proxy=None: FakeOpener(http(403)))
    with pytest.raises(LookupError, match="expired"):
        tk._get("http://j")


def test_client_error_is_not_retried(monkeypatch, no_sleep):
    """404 will be 404 next time too."""
    op = FakeOpener(http(404))
    monkeypatch.setattr(tk, "_opener", lambda proxy=None: op)
    with pytest.raises(LookupError, match="HTTP 404"):
        tk._get("http://j")
    assert op.calls == 1


def test_dns_failure_is_not_retried(monkeypatch, no_sleep):
    """Retrying a settled fact just spends 12 seconds to say the same thing."""
    op = FakeOpener(urllib.error.URLError("getaddrinfo failed"))
    monkeypatch.setattr(tk, "_opener", lambda proxy=None: op)
    with pytest.raises(LookupError, match="caption fetch failed"):
        tk._get("http://j")
    assert op.calls == 1


def test_stalled_socket_is_retried(monkeypatch, no_sleep):
    op = FakeOpener(TimeoutError(), b"ok")
    monkeypatch.setattr(tk, "_opener", lambda proxy=None: op)
    assert tk._get("http://j") == b"ok"
    assert op.calls == 2


def test_connection_reset_is_retried(monkeypatch, no_sleep):
    op = FakeOpener(urllib.error.URLError(ConnectionResetError()), b"ok")
    monkeypatch.setattr(tk, "_opener", lambda proxy=None: op)
    assert tk._get("http://j") == b"ok"


def test_opener_sends_a_user_agent():
    assert any(h[0] == "User-Agent" for h in tk._opener().addheaders)


def test_proxy_is_installed():
    op = tk._opener("http://127.0.0.1:8080")
    assert any(type(h).__name__ == "ProxyHandler" for h in op.handlers)


# --------------------------------------------------------------------------
# URL classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.youtube.com/playlist?list=PLxyz",
    "https://www.youtube.com/@channel/videos",
    "https://www.youtube.com/channel/UCabc/videos",
])
def test_playlist_urls(url):
    assert tk.is_playlist_url(url) is True


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    # Shared from inside a playlist: the video is what was asked for.
    "https://www.youtube.com/watch?v=jNQXAC9IVRw&list=PLxyz&index=4",
    "https://youtu.be/jNQXAC9IVRw",
    "https://www.youtube.com/shorts/jNQXAC9IVRw",
])
def test_video_urls(url):
    assert tk.is_playlist_url(url) is False


def test_expand_leaves_a_video_url_alone(monkeypatch):
    monkeypatch.setattr(tk, "_extract", lambda *a, **k: pytest.fail("should not extract"))
    assert tk.expand("https://youtu.be/jNQXAC9IVRw") == ["https://youtu.be/jNQXAC9IVRw"]


def test_expand_skips_deleted_entries(monkeypatch):
    monkeypatch.setattr(tk, "_extract", lambda *a, **k: {
        "_type": "playlist", "title": "p",
        "entries": [{"url": "http://a"}, None, {"id": "bbbbbbbbbbb"}]})
    assert tk.expand("https://www.youtube.com/playlist?list=PL") == [
        "http://a", "https://www.youtube.com/watch?v=bbbbbbbbbbb"]


def test_expand_of_an_empty_playlist(monkeypatch):
    monkeypatch.setattr(tk, "_extract", lambda *a, **k: {"_type": "playlist", "entries": []})
    with pytest.raises(LookupError, match="no playable videos"):
        tk.expand("https://www.youtube.com/playlist?list=PL")


def test_proxy_reaches_the_extraction_too(monkeypatch):
    """Proxying only the caption fetch still gets the probe IP-blocked."""
    seen = {}
    monkeypatch.setattr(tk, "_extract", lambda url, **k: seen.update(k) or {"id": "x"})
    tk.probe("http://x", "http://p")
    assert seen["proxy"] == "http://p"


def test_no_proxy_leaves_the_environment_alone(monkeypatch):
    """An explicit None would override yt-dlp's own http_proxy handling."""
    seen = {}

    class FakeYDL:
        def __init__(self, opts): seen.update(opts)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False): return {"id": "x"}

    monkeypatch.setattr(tk.yt_dlp, "YoutubeDL", FakeYDL)
    tk._extract("http://x", proxy=None)
    assert "proxy" not in seen


def test_expand_proxies_the_playlist_request(monkeypatch):
    seen = {}
    monkeypatch.setattr(tk, "_extract",
                        lambda url, **k: seen.update(k) or {"_type": "playlist",
                                                            "entries": [{"url": "http://a"}]})
    tk.expand("https://www.youtube.com/playlist?list=PL", "http://p")
    assert seen["proxy"] == "http://p"


def test_probe_rejects_a_playlist(monkeypatch):
    monkeypatch.setattr(tk, "_extract",
                        lambda *a, **k: {"_type": "playlist", "entries": [1, 2, 3]})
    with pytest.raises(LookupError, match="playlist \\(3 videos\\)"):
        tk.probe("https://www.youtube.com/playlist?list=PL")


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------

@pytest.mark.parametrize("ms,want", [
    (0, "00:00"), (61_000, "01:01"), (3_599_000, "59:59"),
    (3_600_000, "1:00:00"), (7_384_000, "2:03:04"),
])
def test_stamp(ms, want):
    assert tk.stamp(ms) == want


def test_slug_is_findable_and_unique():
    assert tk.slug("How to Speak: A Lecture!", "abc123") == "how-to-speak-a-lecture-abc123"


def test_slug_falls_back_to_the_id():
    assert tk.slug("!!!", "abc123") == "abc123"
    assert tk.slug("", "") == "transcript"


def test_slug_is_length_capped():
    assert len(tk.slug("word " * 100, "abc123")) <= 60 + len("-abc123")


def test_slug_has_no_trailing_hyphen_after_the_cut():
    assert "--" not in tk.slug("a" * 59 + " b" * 10, "id")


# --------------------------------------------------------------------------
# document assembly
# --------------------------------------------------------------------------

def doc(**over):
    t = {"title": "T", "video_id": "vid12345678", "url": "u", "source": "auto",
         "lang": "en", "translated": False,
         "punctuated": True, "turns": 1, "duration_ms": 1000, "captions_end_ms": 1000,
         "paragraphs": [{"start_ms": 0, "timestamp": "00:00", "turn": 0, "text": "one"}],
         "text": "one"}
    t.update(over)
    return t


def test_markdown_marks_only_the_change_of_turn():
    t = doc(turns=2, paragraphs=[
        {"start_ms": 0, "timestamp": "00:00", "turn": 0, "text": "a"},
        {"start_ms": 1, "timestamp": "00:01", "turn": 1, "text": "b"},
        {"start_ms": 2, "timestamp": "00:02", "turn": 1, "text": "c"},
    ])
    body = tk.to_markdown(t)
    assert "[00:00] a" in body and "[00:01] >> b" in body and "[00:02] c" in body


def test_markdown_warns_about_machine_translation():
    assert "translated: true" in tk.to_markdown(doc(translated=True))


def test_markdown_warns_about_raw_asr():
    assert "unpunctuated" in tk.to_markdown(doc(punctuated=False))


def test_markdown_omits_turns_when_nobody_takes_one():
    assert "turns:" not in tk.to_markdown(doc(turns=1))


# --------------------------------------------------------------------------
# subtitle output
# --------------------------------------------------------------------------

def cued(*segs):
    return doc(segments=[{"start_ms": s, "end_ms": e, "text": t} for s, e, t in segs])


@pytest.mark.parametrize("ms,want", [
    (0, "00:00:00,000"), (1234, "00:00:01,234"), (61_000, "00:01:01,000"),
    (3_723_456, "01:02:03,456"), (-5, "00:00:00,000"),
])
def test_srt_timestamps(ms, want):
    assert tk._ts(ms, ",") == want


def test_webvtt_uses_a_dot():
    assert tk._ts(1234, ".") == "00:00:01.234"


def test_srt_shape():
    out = tk.to_srt(cued((0, 1500, "Hello there."), (1500, 3000, "General Kenobi.")))
    assert out.startswith("1\n00:00:00,000 --> 00:00:01,500\nHello there.\n")
    assert "2\n00:00:01,500 --> 00:00:03,000\nGeneral Kenobi.\n" in out


def test_vtt_has_its_header():
    assert tk.to_vtt(cued((0, 1000, "hi"))).startswith("WEBVTT\n\n")


def test_overlapping_cues_are_truncated():
    """json3 durations overlap; a player renders that as a caption that lingers."""
    out = tk.to_srt(cued((0, 5000, "first"), (1000, 2000, "second")))
    assert "00:00:00,000 --> 00:00:01,000" in out  # cut at the next cue's start


def test_a_zero_length_cue_still_gets_a_moment():
    """Exactly-equal start and end renders as a flicker or not at all."""
    out = tk.to_srt(cued((1000, 1000, "blink")))
    assert "00:00:01,000 --> 00:00:01,001" in out


def test_subtitle_output_without_segments_says_so():
    """Rather than a KeyError from somewhere in the formatter."""
    with pytest.raises(LookupError, match="segments"):
        tk.to_srt(doc())


def test_segments_are_off_by_default(monkeypatch):
    """Thousands of cues would treble a JSON dump most callers don't want."""
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")])
    assert "segments" not in tk.transcript("http://x")


def test_segments_are_included_on_request(monkeypatch):
    stub_video(monkeypatch, [ev(0, 1000, "Hello."), ev(1000, 500, "Again.")])
    t = tk.transcript("http://x", segments_too=True)
    assert t["segments"] == [{"start_ms": 0, "end_ms": 1000, "text": "Hello."},
                             {"start_ms": 1000, "end_ms": 1500, "text": "Again."}]


@pytest.mark.parametrize("fmt,check", [
    ("srt", lambda s: s.startswith("1\n")),
    ("vtt", lambda s: s.startswith("WEBVTT")),
])
def test_cli_writes_subtitles(monkeypatch, tmp_path, fmt, check):
    stub_video(monkeypatch, [ev(0, 1000, "Hello."), ev(1000, 900, "Bye.")], title="Talk")
    monkeypatch.chdir(tmp_path)
    assert tk.main(["http://x", "-f", fmt]) == 0
    assert check((tmp_path / f"talk-vid12345678.{fmt}").read_text(encoding="utf-8"))


def test_json_flag_still_means_format_json(monkeypatch, capsys):
    """The old spelling has to keep working."""
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")])
    tk.main(["http://x", "-o", "-", "--json"])
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_markdown_ends_with_a_newline():
    assert tk.to_markdown(doc()).endswith("\n")


# --------------------------------------------------------------------------
# transcript(): the library call
# --------------------------------------------------------------------------

def stub_video(monkeypatch, events, *, language="en", duration=600, title="T"):
    monkeypatch.setattr(tk, "probe", lambda url, proxy=None, cookies=None: {
        "title": title, "id": "vid12345678", "language": language, "duration": duration,
        "subtitles": {"en": [{"ext": "json3", "url": "http://j"}]},
        "automatic_captions": {}})
    monkeypatch.setattr(tk, "_get", lambda url, tries=4, proxy=None: payload(*events))


def test_transcript_shape(monkeypatch):
    stub_video(monkeypatch, [ev(0, 1000, "Hello there."), ev(1000, 1000, ">> General Kenobi.")])
    t = tk.transcript("http://x")
    assert t["video_id"] == "vid12345678"
    assert t["source"] == "manual"
    assert t["turns"] == 2
    assert t["text"] == "Hello there. General Kenobi."
    assert t["paragraphs"][0]["timestamp"] == "00:00"


def test_duration_is_the_video_not_the_last_cue(monkeypatch):
    """Captions stop early on a video that ends in music."""
    stub_video(monkeypatch, [ev(0, 1000, "hi")], duration=600)
    t = tk.transcript("http://x")
    assert t["duration_ms"] == 600_000
    assert t["captions_end_ms"] == 1000


def test_duration_falls_back_when_metadata_lacks_it(monkeypatch):
    stub_video(monkeypatch, [ev(0, 1000, "hi")], duration=None)
    assert tk.transcript("http://x")["duration_ms"] == 1000


def test_transcript_is_json_safe(monkeypatch):
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")])
    json.dumps(tk.transcript("http://x"))  # raises if a value is not serialisable


def test_empty_track_is_an_error_not_an_empty_document(monkeypatch):
    stub_video(monkeypatch, [])
    with pytest.raises(LookupError, match="empty"):
        tk.transcript("http://x")


def test_turn_count_ignores_an_opening_marker(monkeypatch):
    """A transcript that opens with ">>" would otherwise report an empty turn 0."""
    stub_video(monkeypatch, [ev(0, 1000, ">> Hello."), ev(1000, 1000, ">> Hi.")])
    assert tk.transcript("http://x")["turns"] == 2


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_writes_a_named_file(monkeypatch, tmp_path, capsys):
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")], title="My Talk")
    monkeypatch.chdir(tmp_path)
    assert tk.main(["http://x"]) == 0
    assert (tmp_path / "my-talk-vid12345678.md").read_text(encoding="utf-8").startswith("---")


def test_cli_stdout_emits_only_the_document(monkeypatch, capsys):
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")])
    assert tk.main(["http://x", "-o", "-"]) == 0
    assert capsys.readouterr().out.startswith("---")


def test_cli_single_json_is_an_object_not_an_array(monkeypatch, capsys):
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")])
    tk.main(["http://x", "-o", "-", "--json"])
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_cli_several_json_documents_become_an_array(monkeypatch, capsys):
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")])
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    tk.main(["http://a", "http://b", "-o", "-", "--json"])
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_cli_several_videos_write_into_a_directory(monkeypatch, tmp_path):
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")], title="Talk")
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    out = tmp_path / "new" / "dir"
    assert tk.main(["http://a", "http://b", "-o", str(out)]) == 0
    assert [p.name for p in out.iterdir()] == ["talk-vid12345678.md"]


def test_cli_keeps_going_after_one_video_fails(monkeypatch, tmp_path, capsys):
    """A private video in the middle of a playlist must not lose the other 199."""
    calls = []

    def flaky(url, lang=None, proxy=None, target=tk.TARGET_WORDS, cookies=None, segments_too=False):
        calls.append(url)
        if url == "http://bad":
            raise LookupError("video unavailable")
        return doc(title="Ok")

    monkeypatch.setattr(tk, "transcript", flaky)
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    rc = tk.main(["http://bad", "http://good", "-o", str(tmp_path)])
    assert rc == 1                       # something failed
    assert len(calls) == 2               # but it did not stop
    assert list(tmp_path.iterdir())      # and the good one landed
    assert "video unavailable" in capsys.readouterr().err


def test_cli_reports_an_unwritable_path(monkeypatch, tmp_path, capsys):
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")])
    blocked = tmp_path / "file.md"
    blocked.write_text("x")
    assert tk.main(["http://x", "-o", str(blocked / "nope.md")]) == 1
    assert "cannot write" in capsys.readouterr().err


def test_cli_error_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(tk, "transcript",
                        lambda *a, **k: (_ for _ in ()).throw(LookupError("nope")))
    assert tk.main(["http://x"]) == 1
    assert "error:" in capsys.readouterr().err


def test_cli_expands_a_playlist(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(tk, "expand",
                        lambda u, proxy=None, cookies=None: ["http://a", "http://b", "http://c"])
    monkeypatch.setattr(tk, "transcript",
                        lambda url, *a, **k: seen.append(url) or doc(title=url[-1]))
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    tk.main(["https://www.youtube.com/playlist?list=PL", "-o", str(tmp_path)])
    assert len(seen) == 3
    assert len(list(tmp_path.iterdir())) == 3


def test_cli_paces_itself_between_videos(monkeypatch, tmp_path):
    """Back to back caption pulls are what trips the per-IP rate limit."""
    slept = []
    monkeypatch.setattr(tk.time, "sleep", slept.append)
    monkeypatch.setattr(tk, "transcript", lambda url, *a, **k: doc(title=url[-1]))
    tk.main(["http://a", "http://b", "http://c", "-o", str(tmp_path)])
    assert len(slept) == 2  # between, not before the first or after the last


def test_list_shows_tracks_and_the_pick(monkeypatch, capsys):
    monkeypatch.setattr(tk, "probe", lambda url, proxy=None, cookies=None: {
        "title": "T", "language": "en",
        "subtitles": {"en": [], "de": []},
        "automatic_captions": {"en": [], "en-orig": [], "fr": []}})
    assert tk._list("http://x") == 0
    out = capsys.readouterr().out
    assert "de, en" in out and "en, en-orig" in out and "-> en (manual)" in out


def test_list_of_a_video_with_nothing_to_pick_still_succeeds(monkeypatch, capsys):
    """Listing answered the question; "there is nothing I'd pick" is the answer.

    Needs a video whose tracks are in neither English nor the spoken language,
    now that the spoken language is a fallback.
    """
    monkeypatch.setattr(tk, "probe", lambda url, proxy=None, cookies=None: {
        "title": "T", "language": "ja", "subtitles": {"de": []}, "automatic_captions": {}})
    assert tk._list("http://x") == 0
    assert "-> (none)" in capsys.readouterr().out


def test_list_shows_the_native_fallback_pick(monkeypatch, capsys):
    monkeypatch.setattr(tk, "probe", lambda url, proxy=None, cookies=None: {
        "title": "T", "language": "de", "subtitles": {"de": []}, "automatic_captions": {}})
    tk._list("http://x")
    assert "-> de (manual)" in capsys.readouterr().out


def test_list_of_an_unavailable_video_fails(monkeypatch, capsys):
    monkeypatch.setattr(tk, "probe",
                        lambda url, proxy=None, cookies=None: (_ for _ in ()).throw(LookupError("private video")))
    assert tk._list("http://x") == 1
    assert "private video" in capsys.readouterr().err


# --------------------------------------------------------------------------
# resuming a batch run
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,want", [
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "jNQXAC9IVRw"),
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw&list=PL&index=2", "jNQXAC9IVRw"),
    ("https://youtu.be/jNQXAC9IVRw", "jNQXAC9IVRw"),
    ("https://www.youtube.com/shorts/jNQXAC9IVRw", "jNQXAC9IVRw"),
    ("https://www.youtube.com/playlist?list=PLxyz", None),
])
def test_video_id_is_read_off_the_url(url, want):
    """Without asking YouTube — that request is the one we're saving."""
    assert tk.video_id(url) == want


def test_existing_matches_on_the_id_not_the_title(tmp_path):
    """The title slug isn't knowable without a probe; the id is."""
    (tmp_path / "some-old-title-vid12345678.md").write_text("x")
    assert tk._existing(str(tmp_path), "vid12345678", "md")
    assert tk._existing(str(tmp_path), "vid12345678", "json") is None
    assert tk._existing(str(tmp_path), "othervideo1", "md") is None


def test_existing_without_an_id_declines_to_guess(tmp_path):
    assert tk._existing(str(tmp_path), None, "md") is None


def test_existing_partial_id_does_not_match(tmp_path):
    """"-<id>.md" so a video whose id is a suffix of another doesn't collide."""
    (tmp_path / "title-xxvid12345678.md").write_text("x")
    assert tk._existing(str(tmp_path), "vid12345678", "md") is None


def test_a_trailing_slash_means_a_directory(monkeypatch, tmp_path):
    """`-o notes/` with one video used to write a *file* called "notes"."""
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")], title="Talk")
    out = tmp_path / "notes"
    assert tk.main(["http://x", "-o", f"{out}/"]) == 0
    assert out.is_dir()
    assert (out / "talk-vid12345678.md").exists()


def test_an_explicit_filename_is_still_a_file(monkeypatch, tmp_path):
    stub_video(monkeypatch, [ev(0, 1000, "Hello.")])
    out = tmp_path / "mine.md"
    tk.main(["http://x", "-o", str(out)])
    assert out.is_file()


def test_skip_existing_with_an_explicit_filename(monkeypatch, tmp_path):
    """-o FILE: "already written" is just whether that file is there."""
    out = tmp_path / "mine.md"
    out.write_text("previously")
    monkeypatch.setattr(tk, "transcript",
                        lambda *a, **k: pytest.fail("should not have fetched"))
    assert tk.main(["https://youtu.be/vid12345678", "-o", str(out),
                    "--skip-existing"]) == 0
    assert out.read_text() == "previously"


def test_skip_existing_with_no_out_looks_in_the_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "whatever-vid12345678.md").write_text("x")
    monkeypatch.setattr(tk, "transcript",
                        lambda *a, **k: pytest.fail("should not have fetched"))
    assert tk.main(["https://youtu.be/vid12345678", "--skip-existing"]) == 0


def test_skip_existing_does_not_fetch(monkeypatch, tmp_path, capsys):
    """The whole point: no request for what's already on disk."""
    (tmp_path / "already-there-vid12345678.md").write_text("x")
    calls = []
    monkeypatch.setattr(tk, "transcript", lambda url, *a, **k: calls.append(url) or doc())
    rc = tk.main(["https://youtu.be/vid12345678",
                  "https://youtu.be/otherone123", "-o", str(tmp_path),
                  "--skip-existing"])
    assert rc == 0
    assert calls == ["https://youtu.be/otherone123"]
    assert "have already-there-vid12345678.md" in capsys.readouterr().err


def test_without_skip_existing_it_refetches(monkeypatch, tmp_path):
    (tmp_path / "already-there-vid12345678.md").write_text("x")
    calls = []
    monkeypatch.setattr(tk, "transcript", lambda url, *a, **k: calls.append(url) or doc())
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    tk.main(["https://youtu.be/vid12345678", "https://youtu.be/otherone123",
             "-o", str(tmp_path)])
    assert len(calls) == 2


def test_skips_are_not_paced(monkeypatch, tmp_path):
    """A resume shouldn't crawl for a second per file it isn't fetching."""
    for i in range(3):
        (tmp_path / f"t-vid1234567{i}.md").write_text("x")
    slept = []
    monkeypatch.setattr(tk.time, "sleep", slept.append)
    monkeypatch.setattr(tk, "transcript", lambda url, *a, **k: doc())
    tk.main([f"https://youtu.be/vid1234567{i}" for i in range(3)]
            + ["https://youtu.be/realone1234", "-o", str(tmp_path), "--skip-existing"])
    assert slept == []  # three skips then one fetch: nothing to pace


# --------------------------------------------------------------------------
# giving up when rate-limited
# --------------------------------------------------------------------------

def test_every_failure_type_is_still_a_lookup_error():
    """Callers who don't care keep the one-exception contract of ADR 0007."""
    for cls in (tk.RateLimited, tk.Unavailable, tk.NoCaptions):
        assert issubclass(cls, LookupError)


@pytest.mark.parametrize("msg,want", [
    # blocked: wait, or use a proxy
    ("HTTP Error 429: Too Many Requests", "RateLimited"),
    ("Sign in to confirm you're not a bot. This helps protect our community.", "RateLimited"),
    ("YouTube is blocking requests from your IP", "RateLimited"),
    # gone: skip it, retrying will never help
    ("Video unavailable", "Unavailable"),
    ("Private video. Sign in if you've been granted access", "Unavailable"),
    ("This video has been removed by the uploader", "Unavailable"),
    ("This video is not available in your country", "Unavailable"),
    ("Join this channel to get access to members-only content", "Unavailable"),
    # the video is fine, the captions aren't there
    ("Subtitles are disabled for this video", "NoCaptions"),
    # nothing recognised: stays the base type rather than guessing
    ("Unable to extract player response", "LookupError"),
])
def test_failures_are_classified(msg, want):
    assert tk._classify(msg).__name__ == want


def test_an_age_gate_is_not_mistaken_for_a_block():
    """The bug this taxonomy found.

    YouTube says "sign in to confirm you're not a bot" for a block and "sign in
    to confirm your age" for an age gate. Matching the shared prefix made every
    age-restricted video abort the entire playlist run, telling the user to wait
    out a rate limit that was not happening.
    """
    msg = "Sign in to confirm your age. This video may be inappropriate for some users."
    assert not tk._looks_rate_limited(msg)
    assert tk._classify(msg) is tk.Unavailable


def test_an_age_gate_names_the_fix(monkeypatch):
    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False):
            raise tk.yt_dlp.utils.DownloadError("ERROR: Sign in to confirm your age")

    monkeypatch.setattr(tk.yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(tk.Unavailable, match="--cookies"):
        tk._extract("http://x")


def test_the_cookie_hint_is_dropped_once_cookies_are_supplied(monkeypatch):
    """Telling someone to pass --cookies when they already did is noise."""
    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False):
            raise tk.yt_dlp.utils.DownloadError("ERROR: Sign in to confirm your age")

    monkeypatch.setattr(tk.yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(tk.Unavailable) as caught:
        tk._extract("http://x", **tk._cookie_opts("firefox"))
    assert "--cookies" not in str(caught.value)


def test_missing_captions_raise_no_captions():
    with pytest.raises(tk.NoCaptions):
        tk.pick_track(info_with())
    with pytest.raises(tk.NoCaptions):
        tk.pick_track(info_with(manual=["de"], language="ja"))
    with pytest.raises(tk.NoCaptions):
        tk.pick_track(info_with(manual=["en"]), "zz")


def test_an_age_gated_video_does_not_abort_a_batch_run(monkeypatch, tmp_path, capsys):
    """The consequence of the misclassification: one age-gated video in a
    playlist killed the other 199."""
    calls = []

    def flaky(url, *a, **k):
        calls.append(url)
        if len(calls) == 2:
            raise tk.Unavailable("Sign in to confirm your age")
        return doc(title=url[-1])

    monkeypatch.setattr(tk, "transcript", flaky)
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    tk.main([f"https://youtu.be/vid123456{i:02d}" for i in range(4)] + ["-o", str(tmp_path)])
    assert len(calls) == 4  # carried on
    assert "not fetched" not in capsys.readouterr().err  # did not report giving up


@pytest.mark.parametrize("msg", [
    "HTTP Error 429: Too Many Requests",
    "Sign in to confirm you're not a bot",
    "YouTube is blocking requests from your IP",
])
def test_block_messages_are_recognised(msg):
    assert tk._looks_rate_limited(msg)


@pytest.mark.parametrize("msg", [
    "Video unavailable", "Private video", "This video is age-restricted",
])
def test_ordinary_failures_are_not_mistaken_for_a_block(msg):
    assert not tk._looks_rate_limited(msg)


def test_a_block_stops_the_run(monkeypatch, tmp_path, capsys):
    """Asking 197 more times makes the block worse and finds nothing."""
    calls = []

    def blocked(url, *a, **k):
        calls.append(url)
        if len(calls) == 2:
            raise tk.RateLimited("YouTube rate-limited the caption fetch (429)")
        return doc(title=url[-1])

    monkeypatch.setattr(tk, "transcript", blocked)
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    rc = tk.main([f"https://youtu.be/vid123456{i:02d}" for i in range(6)]
                 + ["-o", str(tmp_path)])
    assert rc == 1
    assert len(calls) == 2  # stopped, did not try the other four
    err = capsys.readouterr().err
    assert "4 of 6 not fetched" in err
    assert "--skip-existing" in err


def test_an_ordinary_failure_does_not_stop_the_run(monkeypatch, tmp_path):
    """Only a block is grounds for giving up; a private video isn't."""
    calls = []

    def flaky(url, *a, **k):
        calls.append(url)
        if len(calls) == 2:
            raise LookupError("Private video")
        return doc(title=url[-1])

    monkeypatch.setattr(tk, "transcript", flaky)
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    assert tk.main([f"https://youtu.be/vid123456{i:02d}" for i in range(4)]
                   + ["-o", str(tmp_path)]) == 1
    assert len(calls) == 4


def test_batch_run_reports_a_tally(monkeypatch, tmp_path, capsys):
    (tmp_path / "had-vid12345600.md").write_text("x")
    monkeypatch.setattr(tk, "transcript", lambda url, *a, **k: doc(title=url[-1]))
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    tk.main([f"https://youtu.be/vid123456{i:02d}" for i in range(3)]
            + ["-o", str(tmp_path), "--skip-existing"])
    assert "2 written, 1 already had of 3" in capsys.readouterr().err


def test_a_single_video_gets_no_tally(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(tk, "transcript", lambda url, *a, **k: doc())
    tk.main(["https://youtu.be/vid12345678", "-o", str(tmp_path / "one.md")])
    assert " of 1" not in capsys.readouterr().err


def test_extract_maps_a_block_to_rate_limited(monkeypatch):
    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False):
            raise tk.yt_dlp.utils.DownloadError("ERROR: Sign in to confirm you're not a bot")

    monkeypatch.setattr(tk.yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(tk.RateLimited):
        tk._extract("http://x")


def test_extract_leaves_an_ordinary_error_alone(monkeypatch):
    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False):
            raise tk.yt_dlp.utils.DownloadError("ERROR: Video unavailable")

    monkeypatch.setattr(tk.yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(LookupError) as caught:
        tk._extract("http://x")
    assert not isinstance(caught.value, tk.RateLimited)


def test_persistent_429_raises_rate_limited(monkeypatch, no_sleep):
    monkeypatch.setattr(tk, "_opener", lambda proxy=None: FakeOpener(*[http(429)] * 4))
    with pytest.raises(tk.RateLimited):
        tk._get("http://j")


# --------------------------------------------------------------------------
# cookies, --force, --version
# --------------------------------------------------------------------------

def test_no_cookies_means_no_yt_dlp_options():
    assert tk._cookie_opts(None) == {}
    assert tk._cookie_opts("") == {}


def test_a_browser_name_becomes_cookiesfrombrowser():
    assert tk._cookie_opts("firefox") == {"cookiesfrombrowser": ("firefox", None, None, None)}


def test_a_browser_profile_is_split_off():
    got = tk._cookie_opts("chrome:Profile 1")
    assert got == {"cookiesfrombrowser": ("chrome", "Profile 1", None, None)}


def test_an_existing_path_becomes_a_cookie_file(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File")
    assert tk._cookie_opts(str(jar)) == {"cookiefile": str(jar)}


def test_cookies_reach_the_extraction(monkeypatch):
    """The --proxy lesson: an option the CLI accepts and never passes on is worse
    than one it doesn't offer."""
    seen = {}
    monkeypatch.setattr(tk, "_extract", lambda url, **k: seen.update(k) or {"id": "x"})
    tk.probe("http://x", None, "firefox")
    assert seen["cookiesfrombrowser"] == ("firefox", None, None, None)


def test_cookies_reach_the_playlist_expansion(monkeypatch):
    seen = {}
    monkeypatch.setattr(tk, "_extract", lambda url, **k: seen.update(k) or {
        "_type": "playlist", "entries": [{"url": "http://a"}]})
    tk.expand("https://www.youtube.com/playlist?list=PL", None, "firefox")
    assert "cookiesfrombrowser" in seen


def test_cli_passes_cookies_through(monkeypatch, tmp_path):
    got = {}

    def spy(url, lang=None, proxy=None, target=None, cookies=None, segments_too=False):
        got["cookies"] = cookies
        return doc()

    monkeypatch.setattr(tk, "transcript", spy)
    tk.main(["http://x", "-o", str(tmp_path / "o.md"), "--cookies", "firefox"])
    assert got["cookies"] == "firefox"


def test_force_overrides_skip_existing(monkeypatch, tmp_path):
    """Captions do get corrected; --skip-existing alone would keep the old file."""
    (tmp_path / "old-vid12345678.md").write_text("stale")
    calls = []
    monkeypatch.setattr(tk, "transcript", lambda url, *a, **k: calls.append(url) or doc())
    tk.main(["https://youtu.be/vid12345678", "-o", str(tmp_path),
             "--skip-existing", "--force"])
    assert len(calls) == 1


def test_version_is_reported(capsys):
    with pytest.raises(SystemExit) as caught:
        tk.main(["--version"])
    assert caught.value.code == 0
    assert "transkrp" in capsys.readouterr().out


def test_cli_passes_the_word_target_through(monkeypatch, tmp_path):
    got = {}

    def spy(url, lang=None, proxy=None, target=None, cookies=None, segments_too=False):
        got["target"], got["proxy"] = target, proxy
        return doc()

    monkeypatch.setattr(tk, "transcript", spy)
    tk.main(["http://x", "-o", str(tmp_path / "o.md"), "--words", "40",
             "--proxy", "http://p"])
    assert got == {"target": 40, "proxy": "http://p"}
