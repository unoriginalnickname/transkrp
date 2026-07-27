"""Fetch a YouTube transcript as timestamped paragraphs.

CLI:
    python transkrp.py URL... [-o OUT] [--lang KEY] [--json] [--list]

Library:
    from transkrp import transcript
    t = transcript("https://youtube.com/watch?v=...")   # -> dict, JSON-safe
    t["paragraphs"][0]["text"]

yt-dlp does the fetching and already prefers manual captions over auto ones.
This adds what it doesn't do: read the json3 format, which unlike .vtt doesn't
repeat every phrase as the caption box scrolls, and reflow the cues into
readable paragraphs.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

import yt_dlp

GAP_BREAK_MS = 2000  # a silence this long reads as a topic break, not a breath
TARGET_WORDS = 110
MIN_WORDS = 25  # don't let a pause strand a two-word paragraph
TIMEOUT = 30  # seconds; without one, urlopen waits on a stalled socket forever
# YouTube throttles unidentified clients harder. yt-dlp sends its own UA on the
# metadata probe; the caption fetch is ours, so it needs one too.
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


# Three failures worth telling apart, because each implies a different next move:
# wait, skip this video forever, or try another track. Everything else stays a
# plain LookupError, so `except LookupError` remains the whole contract for
# callers who don't care (ADR 0007).
class RateLimited(LookupError):
    """YouTube is refusing us for volume, not for anything about this video.

    Batch runs care: every remaining video will fail the same way, and trying
    them makes the block worse. Wait, or use a proxy.
    """


class Unavailable(LookupError):
    """This video cannot be read, and no amount of retrying will change that.

    Private, deleted, region-locked, or age-gated without cookies. Skip it.
    """


class NoCaptions(LookupError):
    """The video is fine; there is no caption track we can use.

    Distinct from Unavailable because the video may well have captions in some
    other language — `--lang` is the move, not giving up.
    """


class _Silent:  # yt-dlp logs errors itself; we raise them instead
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def _extract(url: str, **extra) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "logger": _Silent(), **extra}
    # yt-dlp reads `proxy` from the environment when the key is absent; an
    # explicit None would override that with "no proxy".
    if opts.get("proxy") is None:
        opts.pop("proxy", None)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:  # unavailable, private, bad URL
        msg = _tidy(str(e))
        have_cookies = opts.get("cookiefile") or opts.get("cookiesfrombrowser")
        if _NEEDS_AUTH.search(msg) and not have_cookies:
            msg += " - if you have access, retry with --cookies firefox (or your browser)"
        raise _classify(msg)(msg) from e


def _tidy(msg: str) -> str:
    """Cut yt-dlp's advice, which names yt-dlp's flags rather than ours.

    A private video arrives as 457 characters ending in two wiki URLs and "Use
    --cookies-from-browser or --cookies for the authentication" — and this CLI
    has no --cookies-from-browser. Pointing someone at an option that does not
    exist is worse than saying nothing, and it is exactly the failure ADR 0007
    exists to prevent. The video id goes too: the caller already prints the URL.
    """
    msg = msg.replace("ERROR: ", "")
    msg = re.split(r"\s*(?:Use --cookies|(?:Also s|S)ee\s+https?://)", msg)[0]
    msg = re.sub(r"^\[[\w:]+\]\s*[\w-]{11}:\s*", "", msg.strip())
    return msg.strip().rstrip(".")


# yt-dlp reports all of this as free text, so this is pattern-matching on prose.
# It decides whether a batch run gives up, skips, or carries on, so a miss costs
# wasted requests or a needless abort rather than a wrong transcript.
#
# "Sign in to confirm" is deliberately NOT a block signal on its own: YouTube
# says "sign in to confirm you're not a bot" for a block and "sign in to confirm
# your age" for an age gate. Matching the shared prefix classified every
# age-restricted video as a rate limit, which aborted the whole playlist and told
# the user to wait out a block that was not happening.
_BLOCKED = re.compile(r"429|too many requests|rate.?limit|not a bot|"
                      r"block\w*[^.]*\bip\b|\bip\b[^.]*block", re.I)
_GONE = re.compile(r"video unavailable|private video|has been removed|"
                   r"account.*terminated|not available in your country|"
                   r"confirm your age|age.?restricted|members[- ]only", re.I)
_NO_SUBS = re.compile(r"subtitles are disabled|no subtitles", re.I)
# The Unavailables a user can actually do something about, given an account.
_NEEDS_AUTH = re.compile(r"confirm your age|age.?restricted|"
                         r"inappropriate for some users|private video|"
                         r"members[- ]only", re.I)


def _classify(msg: str) -> type[LookupError]:
    if _BLOCKED.search(msg):
        return RateLimited
    if _NO_SUBS.search(msg):
        return NoCaptions
    if _GONE.search(msg):
        return Unavailable
    return LookupError


def _cookie_opts(cookies: str | None) -> dict:
    """yt-dlp options for --cookies, which takes a browser name or a file path.

    Age-gated and sign-in-required videos are the reason: without cookies they
    fail at the probe with "Sign in to confirm your age" and there is nothing to
    fetch. A path is a Netscape cookie jar; anything else is read as a browser
    name, the same spelling yt-dlp uses (`firefox`, `chrome:Profile 1`).
    """
    if not cookies:
        return {}
    if os.path.exists(cookies):
        return {"cookiefile": cookies}
    browser, _, profile = cookies.partition(":")
    return {"cookiesfrombrowser": (browser.lower(), profile or None, None, None)}


def probe(url: str, proxy: str | None = None, cookies: str | None = None) -> dict:
    """Video metadata, including the index of caption tracks.

    `noplaylist` because a video shared from inside a playlist carries `&list=`,
    and yt-dlp would otherwise extract all 200 of its neighbours.
    """
    info = _extract(url, noplaylist=True, proxy=proxy, **_cookie_opts(cookies))
    if info.get("_type") == "playlist":
        n = len(info.get("entries") or [])
        raise LookupError(f"that URL is a playlist ({n} videos), not a video")
    return info


# A single video shared from inside a playlist carries `&list=`, so a `v=` id
# wins: pasting YouTube's share link should fetch the video you were watching,
# not the 200 around it.
_VIDEO_URL = re.compile(r"[?&]v=[\w-]{11}|youtu\.be/[\w-]{11}|/shorts/[\w-]{11}")
_LIST_URL = re.compile(r"/playlist|/channel/|/@|/c/|/user/|[?&]list=", re.I)


def is_playlist_url(url: str, force: bool = False) -> bool:
    """Does this URL name several videos rather than one?

    A `v=` id wins over `list=` unless `force` says otherwise, so pasting
    YouTube's share link fetches the video you were watching rather than the 200
    around it. `--playlist` is the override, and `is_ambiguous` below is why it
    had to exist: the assumption is right often, not always.
    """
    if force and _LIST_URL.search(url):
        return True
    return not _VIDEO_URL.search(url) and bool(_LIST_URL.search(url))


def is_ambiguous(url: str) -> bool:
    """A URL naming a video *and* the playlist it sits in — it could mean either.

    Worth saying out loud rather than silently picking. The first person to hand
    this tool such a URL called it "a playlist" and meant all 40 videos; the
    default gave them 1, correctly by the documented rule and wrongly by intent.
    Guessing better is impossible, so the fix is to say which reading was used.
    """
    return bool(_VIDEO_URL.search(url) and _LIST_URL.search(url))


def video_id(url: str) -> str | None:
    """The 11-character id, read off the URL without asking YouTube.

    This is what makes --skip-existing worth having: the id is in every output
    filename, so a rerun can tell what it already has *before* spending the
    request that would be rate-limited.
    """
    m = _VIDEO_URL.search(url)
    return m.group(0)[-11:] if m else None


def expand(url: str, proxy: str | None = None, cookies: str | None = None,
           force_playlist: bool = False) -> list[str]:
    """Resolve one URL to video URLs; a playlist or channel becomes its entries.

    Flat extraction, so a 200-video playlist costs one request instead of 200 —
    each video is probed later anyway, and only if we get that far.
    """
    if not is_playlist_url(url, force_playlist):
        return [url]
    info = _extract(url, extract_flat="in_playlist", proxy=proxy, **_cookie_opts(cookies))
    if info.get("_type") != "playlist":
        return [url]
    out = []
    for e in info.get("entries") or []:
        if not e:
            continue  # deleted and private entries come back as None
        vid = e.get("url") or e.get("webpage_url")
        if not vid and e.get("id"):
            vid = f"https://www.youtube.com/watch?v={e['id']}"
        if vid:
            out.append(vid)
    if not out:
        raise LookupError(f"playlist {info.get('title') or url!r} has no playable videos")
    return out


def pick_track(info: dict, want: str | None = None) -> tuple[str, str, bool]:
    """Return (source, key, translated). Manual wins; it needs no repair.

    English first, then the language the video is actually in. Preferring English
    is a default, not a requirement: erroring out on a German video when a German
    track is sitting right there made the user run --list and pass --lang to
    learn something we already knew.

    `--lang auto` skips the English preference and takes the spoken language.
    """
    manual, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}

    if want and want != "auto":
        if want in manual:
            return "manual", want, False
        if want in auto:
            return "auto", want, _translated(info, want)
        raise NoCaptions(f"no caption track {want!r}; manual tracks are "
                          f"{_names(manual)} (see --list for the auto ones)")

    for source, prefix in _preference(info, want):
        tracks = manual if source == "manual" else auto
        # Keys aren't always the bare code: there's en-orig (original spoken
        # track), en-US/en-GB, and multi-track videos expose en-<trackid>.
        matches = [k for k in tracks if k.split("-", 1)[0].lower() == prefix]
        if matches:
            key = next((k for k in (prefix, f"{prefix}-orig", f"{prefix}-US",
                                    f"{prefix}-GB") if k in matches), matches[0])
            return source, key, _translated(info, key) if source == "auto" else False

    # Say what there *is*: with nothing in English and nothing in the spoken
    # language, --lang is the fix and the user needs to know what to pass.
    if manual or auto:
        spoken = _lang_of(info)
        raise NoCaptions(f"no captions in English{f' or {spoken}' if spoken else ''}; "
                          f"manual tracks are {_names(manual)} - pass --lang to pick one")
    raise NoCaptions("this video has no captions at all")


def _lang_of(info: dict) -> str:
    """The video's own language as a bare prefix: 'en-US' -> 'en'."""
    return (info.get("language") or "").split("-", 1)[0].lower()


def _preference(info: dict, want: str | None) -> list[tuple[str, str]]:
    """(source, language) pairs to try, best first.

    The ordering that matters is inside the auto tracks. YouTube lists ~150
    machine translations of its ASR alongside the original, so a German video
    offers an "en" auto track — and taking it means a machine translation of a
    machine transcription when the original German was right there. Two lossy
    steps where one would do, and it reads like it.

    So: human transcription first in either language, then the *original* ASR,
    and a translation only as a last resort. A manual English track on a German
    video is a real human translation and is genuinely preferable; a machine one
    is not.
    """
    native = _lang_of(info)
    order = []
    if want != "auto":
        order.append(("manual", "en"))
    if native:
        order += [("manual", native), ("auto", native)]
    if want != "auto":
        order.append(("auto", "en"))
    if not native:  # language unknown: nothing to prefer but English
        order = [p for p in order if p[1] == "en"]
    return list(dict.fromkeys(order))


def _names(tracks: dict, limit: int = 8) -> str:
    keys = sorted(tracks)
    if not keys:
        return "(none)"
    shown = ", ".join(keys[:limit])
    return shown if len(keys) <= limit else f"{shown}, +{len(keys) - limit} more"


def _clean(text: str) -> str:
    """Normalise caption typography.

    Captions carry U+2011 non-breaking hyphens and U+00A0 spaces that look
    ordinary but aren't: downstream, "real<U+2011>time" won't match a search for
    "real-time" and tokenises differently. Fix at the source.
    """
    text = text.replace("‑", "-").replace("‐", "-")
    text = text.replace(" ", " ").replace("​", "")
    # Cues carry hard newlines from the two-line caption box. Those are breaks in
    # the display, not in the sentence.
    return re.sub(r"\s+", " ", text)


def _translated(info: dict, key: str) -> bool:
    """Is this auto track a machine translation, or the original ASR?

    YouTube auto-translates its ASR into ~150 languages and lists them all under
    automatic_captions, so an "en" entry on a Japanese video is a machine
    translation of a machine transcription — two lossy steps, not one.

    Comparing the *track's* language to the video's is what tells those apart. It
    used to compare only the video's language against English, which was right
    while English was the only thing we ever picked; now that we fall back to the
    spoken language, that would label a German track on a German video a
    translation of itself.
    """
    native = _lang_of(info)
    return bool(native) and key.split("-", 1)[0].lower() != native


def _transient(err: BaseException) -> bool:
    """Is this worth retrying, or is it the same answer every time?

    A stalled socket or a dropped connection will likely work on the next try.
    DNS failure and TLS refusal are settled facts: retrying spends 12 seconds to
    print the same message.
    """
    reason = getattr(err, "reason", err)
    return isinstance(reason, (TimeoutError, ConnectionError))


def _opener(proxy: str | None = None):
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    op = urllib.request.build_opener(*handlers)
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "en-US,en;q=0.9")]
    return op


def _get(url: str, tries: int = 4, proxy: str | None = None) -> bytes:
    """Fetch the caption JSON, retrying transient failures with backoff.

    YouTube rate-limits repeated caption pulls (empirically a few hundred an
    hour) and blocks datacenter IP ranges outright, which is what `--proxy` is
    for. 429 and a stalled socket are transient, so back off and retry; surface
    anything else as a clean LookupError rather than a urllib traceback.
    """
    op = _opener(proxy)
    for attempt in range(tries):
        last = tries - 1
        try:
            with op.open(url, timeout=TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as e:  # a subclass of URLError; catch first
            if e.code in (429, 500, 502, 503) and attempt < last:
                # A server that says when to come back beats our guess.
                time.sleep(_retry_after(e) or _backoff(attempt))
                continue
            if e.code == 429:
                raise RateLimited("YouTube rate-limited the caption fetch (429) - "
                                  "wait a few minutes, or use --proxy from a "
                                  "datacenter IP") from e
            if e.code == 403:
                # Caption URLs are signed and carry ip/expire/signature: they are
                # bound to the fetching IP and good for only a few hours.
                raise LookupError("caption URL rejected (HTTP 403) - it expired or "
                                  "was issued for a different IP; refetch") from e
            raise LookupError(f"caption fetch failed (HTTP {e.code})") from e
        except urllib.error.URLError as e:
            if _transient(e) and attempt < last:
                time.sleep(_backoff(attempt))
                continue
            raise LookupError(f"caption fetch failed: {e.reason}") from e
        except TimeoutError as e:  # raised bare when the read, not the connect, stalls
            if attempt < last:
                time.sleep(_backoff(attempt))
                continue
            raise LookupError(f"caption fetch timed out after {TIMEOUT}s") from e
    raise LookupError("caption fetch failed after retries")


MAX_RETRY_AFTER = 60  # past this, waiting is worse than telling the user to retry


def _retry_after(err: urllib.error.HTTPError) -> float | None:
    """Seconds the server asked us to wait, if it asked and the answer is sane.

    RFC 9110 allows either a delay in seconds or an HTTP-date. Only the numeric
    form is honoured: the date form needs the server's clock, and a skewed one
    would park the process for hours. Capped for the same reason — a
    `Retry-After: 3600` is information, not an instruction to hang for an hour.
    """
    value = (err.headers or {}).get("Retry-After") if hasattr(err, "headers") else None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return min(seconds, MAX_RETRY_AFTER) if seconds > 0 else None


def _backoff(attempt: int) -> float:
    """Exponential with jitter: 2s, 4s, 8s, give or take.

    Flat 2s steps are too impatient for a real rate-limit, and identical delays
    across concurrent runs re-collide on every retry.
    """
    return 2 ** (attempt + 1) * (0.75 + random.random() / 2)


def segments(info: dict, source: str, key: str, proxy: str | None = None) -> list[tuple[int, int, str]]:
    fmts = info["subtitles" if source == "manual" else "automatic_captions"][key]
    json3 = next((f for f in fmts if f.get("ext") == "json3"), None)
    if not json3:
        raise NoCaptions(f"track {key!r} has no json3 format")
    body = _get(json3["url"], proxy=proxy)

    # The interesting failure here is not an error status. When the timedtext
    # endpoint wants a PO token it doesn't have, it answers 200 with an empty
    # body — so an unguarded json.loads dies on "Expecting value: line 1
    # column 1" and tells the user nothing about what went wrong.
    if not body.strip():
        raise LookupError(
            f"YouTube returned an empty caption track for {key!r} - it usually means "
            "the request needs a PO token; upgrading yt-dlp is the fix")
    try:
        events = json.loads(body.decode("utf-8", "replace")).get("events") or []
    except json.JSONDecodeError as e:
        raise LookupError(f"caption track {key!r} was not valid json3 ({e.args[0]})") from e

    out = []
    for e in events:
        # aAppend events drive the scrolling two-line display — the same
        # animation that makes a naive .vtt strip repeat every phrase two or
        # three times. But json3 expresses it as an append of a bare newline
        # instead of re-emitting the text, so the duplication never arrives here
        # at all (measured across three videos: 1371 of 2744 events on an
        # hour-long lecture, not one of them carrying a word).
        #
        # Which makes this check belt-and-braces: `if text` below would drop them
        # anyway. It stays because aAppend *means* "a continuation of what's
        # already on screen", and a track that ever put words in one would
        # duplicate silently. Cheap, and it states the intent. See ADR 0002.
        if e.get("aAppend") == 1 or not e.get("segs"):
            continue
        text = _clean("".join(s.get("utf8", "") for s in e["segs"])).strip()
        if text:
            start = int(e.get("tStartMs", 0))
            out.append((start, start + int(e.get("dDurationMs") or 0), text))
    return out


def is_punctuated(segs: list[tuple[int, int, str]]) -> bool:
    """Does this track have sentence punctuation to break on?

    Detected, never inferred from manual/auto. Neither implies the other:
    YouTube's newer ASR emits punctuation and speaker markers, while some manual
    tracks are unedited dumps with neither.
    """
    if not segs:
        return False
    ends = sum(1 for _, _, text in segs if re.search(r"[.!?][\"')\]]?\s*$", text))
    return ends / len(segs) >= 0.05


def _split_turns(segs: list[tuple[int, int, str]]) -> list[tuple[int, int, str, bool]]:
    """Split segments at '>>' so a speaker change never sits mid-segment.

    Returns the segments with a `starts_turn` flag. '>>' is the caption
    convention for "a different person is talking now" — it says the speaker
    changed, not who they are, so we keep the boundary and claim nothing more.

    Measured on a 5,180-cue congressional hearing: 474 cues carry '>>' and every
    single one has it at the start, so the mid-cue split below never fires in
    practice. Kept because a marker mid-cue would otherwise put two speakers in
    one paragraph silently, and the cost of the branch is nothing. Anyone
    tempted to "fix" the parts sharing a timestamp: they don't, because there is
    never more than one.
    """
    out = []
    for s_ms, e_ms, text in segs:
        parts = [p.strip() for p in re.split(r">>+", text)]
        # A leading '>>' yields an empty first part: the turn starts here.
        lead_marker = bool(parts) and parts[0] == ""
        parts = [p for p in parts if p]
        for i, part in enumerate(parts):
            out.append((s_ms, e_ms, part, lead_marker or i > 0))
    return out


def paragraphs(segs: list[tuple[int, int, str]], punctuated: bool | None = None,
               target: int = TARGET_WORDS) -> list[tuple[int, int, str]]:
    """Group segments into (start_ms, turn, text).

    Breaks at speaker changes first, then long silences, then length. With
    punctuation we can wait for a sentence end, so the cap is loose; raw ASR has
    none, so the cap alone decides — a loose one there gives 160-word walls.
    """
    if punctuated is None:
        punctuated = is_punctuated(segs)
    target = max(target, MIN_WORDS)
    cap = target * 2 if punctuated else target
    out, buf, start, prev_end, words, turn = [], [], 0, None, 0, 0

    def flush():
        nonlocal buf, words
        if buf:
            out.append((start, turn, re.sub(r"\s+", " ", " ".join(buf)).strip()))
            buf, words = [], 0

    for s_ms, e_ms, text, starts_turn in _split_turns(segs):
        if starts_turn:
            # A speaker change always breaks, however short the last turn was.
            flush()
            turn += 1
        # A pause only breaks if there's a paragraph there yet; otherwise a
        # short utterance before a silence is stranded on its own.
        elif words >= MIN_WORDS and prev_end is not None and s_ms - prev_end >= GAP_BREAK_MS:
            flush()
        if not buf:
            start = s_ms
        buf.append(text)
        words += len(text.split())
        prev_end = e_ms
        if punctuated and words >= target and re.search(r"[.!?][\"')\]]?\s*$", text):
            flush()
        elif words >= cap:
            flush()

    flush()
    # A paragraph of nothing but "[music]" or "[applause]" is not a turn — the
    # tag is worth keeping inline, but not as an entry of its own.
    return [p for p in out if not re.fullmatch(r"(\[[^\]]*\]|\s)+", p[2])]


def transcript(url: str, lang: str | None = None, proxy: str | None = None,
               target: int = TARGET_WORDS, cookies: str | None = None,
               segments_too: bool = False) -> dict:
    """Fetch a transcript. The one call another system needs.

    Returns a JSON-safe dict:
        title, video_id, url, source ("manual"|"auto"), lang, translated,
        duration_ms, paragraphs: [{start_ms, timestamp, turn, text}], text

    Raises LookupError if the video is unavailable or has no English captions.
    """
    info = probe(url, proxy, cookies)
    source, key, translated = pick_track(info, lang)
    # No cookies on the caption fetch, and that is not the --proxy oversight
    # repeated: the timedtext URL is already signed by the player response that
    # the cookied probe obtained. Authorisation is baked into the URL.
    segs = segments(info, source, key, proxy=proxy)
    if not segs:
        raise NoCaptions(f"caption track {key!r} was empty")
    # "auto" does not mean "raw": YouTube's newer ASR emits punctuation and >>
    # speaker markers, while some manual tracks are unedited dumps that don't.
    # Detect it; don't infer it from the track's source.
    punctuated = is_punctuated(segs)
    paras = paragraphs(segs, punctuated, target)
    # The last cue's end is where the *captions* stop, which on a video that ends
    # in music is minutes short of the runtime. Prefer what the metadata says.
    duration = info.get("duration")
    return {
        # Provenance. A knowledge file that doesn't say who said this, or when,
        # is a quote with the attribution torn off.
        "channel": info.get("uploader") or info.get("channel") or "",
        "upload_date": _date(info.get("upload_date")),
        # The description is where the guest is named, spelled correctly, by a
        # human. That matters more than it sounds: ASR mangles exactly the words
        # a cross-referencing corpus needs most — this video's description opens
        # "Daniel Peter Sheehan is a Harvard trained constitutional..." while the
        # transcript says "Danny shean". Kept whole in JSON; the first sentence
        # goes in the markdown frontmatter, which is the part naming the guest.
        "description": (info.get("description") or "").strip(),
        # The creator's own section titles, which YouTube has and we were
        # throwing away. Verbatim structure for free: a 33,000-word interview
        # has 19 of these, and without them it is one undifferentiated wall.
        "chapters": _chapters(info),
        "title": info.get("title", ""),
        "video_id": info.get("id", ""),
        "url": url,
        "source": source,
        "lang": key,
        # An "en" auto track on a non-English video is a machine translation of
        # a machine transcription. Callers should treat it with suspicion.
        "translated": translated,
        "punctuated": punctuated,
        "duration_ms": int(duration * 1000) if duration else segs[-1][1],
        "captions_end_ms": segs[-1][1],
        # turn increments at each ">>" speaker change; it marks that the speaker
        # changed, not who. Count the turns that actually carry text: a
        # transcript opening with ">>" would otherwise report an empty turn 0.
        "turns": len({turn for _, turn, _ in paras}),
        "paragraphs": [
            {"start_ms": ms, "timestamp": stamp(ms), "turn": turn, "text": text}
            for ms, turn, text in paras
        ],
        "text": " ".join(text for _, _, text in paras),
        # Off by default: a long video has thousands of cues, which would treble
        # the size of a JSON dump that most callers only want paragraphs from.
        **({"segments": [{"start_ms": s, "end_ms": e, "text": txt}
                         for s, e, txt in segs]} if segments_too else {}),
    }


def _date(yyyymmdd: str | None) -> str:
    """yt-dlp gives 20240822; a human reads 2024-08-22."""
    if not (yyyymmdd and len(yyyymmdd) == 8 and yyyymmdd.isdigit()):
        return ""
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _first_sentence(text: str, limit: int = 220) -> str:
    """The opening sentence of a description, flattened to one line.

    Descriptions run to thousands of characters of links and sponsor copy, but
    the first sentence is reliably who this is: "Our incredible guest today is
    Nick Cook." That belongs in the frontmatter; the rest belongs in the JSON.
    """
    first = re.split(r"(?<=[.!?])\s|\n", text.strip(), maxsplit=1)[0]
    first = re.sub(r"\s+", " ", first).strip()
    return first[:limit].rstrip() + "..." if len(first) > limit else first


def _chapters(info: dict) -> list[dict]:
    """The creator's section titles, as [{start_ms, timestamp, title}].

    yt-dlp invents "<Untitled Chapter 1>" to fill gaps where the creator titled
    some sections but not others. A heading that says nothing is worse than no
    heading, so those are dropped rather than rendered.
    """
    out = []
    for c in info.get("chapters") or []:
        title = (c.get("title") or "").strip()
        if not title or re.fullmatch(r"<untitled chapter \d+>", title, re.I):
            continue
        ms = int(float(c.get("start_time") or 0) * 1000)
        out.append({"start_ms": ms, "timestamp": stamp(ms), "title": title})
    return out


def slug(title: str, video_id: str, limit: int = 60) -> str:
    """A findable filename: title slug + id.

    The slug makes it browsable in a directory of knowledge files; the id keeps
    it unique and traceable back to the video.
    """
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:limit].strip("-")
    return f"{s}-{video_id}" if s else (video_id or "transcript")


def to_markdown(t: dict) -> str:
    head = ["---", f"title: {t['title']}"]
    if t.get("channel"):
        head.append(f"channel: {t['channel']}")
    if t.get("upload_date"):
        head.append(f"published: {t['upload_date']}")
    if about := _first_sentence(t.get("description", "")):
        head.append(f"about: {about}")
    head += [f"url: {t['url']}",
             f"source: {t['source']}", f"lang: {t['lang']}",
             f"punctuated: {str(t['punctuated']).lower()}"]
    if t["translated"]:
        head.append("translated: true  # machine translation of machine transcription")
    if t.get("speakers"):
        head.append(f"speakers: {', '.join(t['speakers'])}")
        head.append(f"speakers_inferred_by: {t.get('speakers_by', '')}  "
                    f"# attribution is generated, not from the captions; "
                    f"'?' marks low confidence")
    elif t["turns"] > 1:
        head.append(f"turns: {t['turns']}  # speaker changes; who is speaking is not marked")
    if not t["punctuated"]:
        head.append("note: unpunctuated speech recognition - no sentence breaks or speaker labels")
    head.append("---")

    # Mark a speaker change with ">>" — the standard transcript convention, and
    # greppable. A horizontal rule per turn would mean 500+ rules in a long
    # interview, which is heavier than the dialogue it annotates. The dict's
    # `text` stays clean; the marker is presentation.
    # Chapters become headings. They are the creator's own section titles, so
    # this is structure the document already had and was discarding — a
    # 33,000-word interview reads as one wall without them.
    pending = list(t.get("chapters") or [])
    lines, prev_turn, prev_speaker = [], None, None
    for p in t["paragraphs"]:
        while pending and pending[0]["start_ms"] <= p["start_ms"]:
            lines.append(f"## {pending.pop(0)['title']}")
        # A name beats the anonymous ">>": it's the whole point of --speakers,
        # and it's what makes a paragraph cross-referenceable to a person. The
        # "?" marks a low-confidence guess rather than hiding it — an inferred
        # attribution presented as certain is the failure mode worth avoiding.
        if speaker := p.get("speaker"):
            if speaker != prev_speaker:
                marker = f"**{speaker}"
                marker += "?**: " if p.get("speaker_confidence") == "low" else "**: "
            else:
                marker = ""
            prev_speaker = speaker
        else:
            new_turn = prev_turn is not None and p["turn"] != prev_turn
            marker = ">> " if new_turn else ""
        # The timestamp links into the video at that second. The whole point of
        # anchoring every paragraph is that a load-bearing claim can be checked
        # against the audio, and that is a different proposition when it is one
        # click rather than a manual scrub.
        lines.append(f"[{p['timestamp']}]({_at(t['url'], p['start_ms'])}) {marker}{p['text']}")
        prev_turn = p["turn"]
    return "\n".join(head) + f"\n\n# {t['title']}\n\n" + "\n\n".join(lines) + "\n"


def _at(url: str, ms: int) -> str:
    """The same video, at that second.

    Rebuilt from the id rather than appended to the given URL, which may already
    carry &list=, &index=, a playlist position, or its own &t= from wherever it
    was copied.
    """
    vid = video_id(url)
    base = f"https://www.youtube.com/watch?v={vid}" if vid else url.split("&")[0]
    return f"{base}&t={ms // 1000}s" if vid else base


def to_srt(t: dict) -> str:
    """A subtitle file from the cleaned cues.

    Worth having even though yt-dlp will hand you a .vtt directly: that one is
    the scrolling two-line box serialised frame by frame, so every phrase appears
    two or three times. These cues came from json3, which doesn't do that, and
    they have had the lookalike typography normalised. Same subtitles, without
    the triplication.
    """
    out = []
    for i, (start, end, text) in enumerate(_cues(t), 1):
        out.append(f"{i}\n{_ts(start, ',')} --> {_ts(end, ',')}\n{text}\n")
    return "\n".join(out)


def to_vtt(t: dict) -> str:
    body = "\n".join(f"{_ts(s, '.')} --> {_ts(e, '.')}\n{text}\n"
                     for s, e, text in _cues(t))
    return f"WEBVTT\n\n{body}"


def _cues(t: dict) -> list[tuple[int, int, str]]:
    """Cue timings fit for a subtitle file.

    json3 durations overlap and occasionally collapse to zero, which players
    render as a flicker or a caption that never leaves. Truncate each cue at the
    next one's start and give a zero-length cue somewhere to live.
    """
    segs = t.get("segments")
    if segs is None:
        raise LookupError("this transcript has no segments; fetch it with "
                          "segments=True (the CLI does this for --format srt/vtt)")
    fixed = []
    for i, s in enumerate(segs):
        start, end = s["start_ms"], s["end_ms"]
        nxt = segs[i + 1]["start_ms"] if i + 1 < len(segs) else None
        if nxt is not None and end > nxt:
            end = nxt
        fixed.append((start, max(end, start + 1), s["text"]))
    return fixed


def _ts(ms: int, frac: str) -> str:
    """SRT wants 00:00:01,234 and WebVTT wants 00:00:01.234."""
    ms = max(int(ms), 0)
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}{frac}{ms:03d}"


def stamp(ms: int) -> str:
    t = ms // 1000
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t % 3600 // 60:02d}:{t % 60:02d}"


def _version() -> str:
    """Installed version, or a marker when running from a checkout.

    The distribution and the module are both `transkrp`. Kept explicit rather than
    derived from __name__, which is "__main__" when run as a script.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version("transkrp")
    except (ImportError, PackageNotFoundError):
        return "(from source)"


def render(t: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(t, indent=2, ensure_ascii=False)
    return {"md": to_markdown, "srt": to_srt, "vtt": to_vtt}[fmt](t)


def _already_written(url: str, out_dir: str | None, explicit: str | None,
                     ext: str) -> str | None:
    """Where this video's output already is, if it is anywhere.

    Three shapes to cover: `-o DIR` (look for the id in it), `-o FILE` (does that
    file exist), and no `-o` at all (look for the id in the working directory,
    which is where the default name lands).
    """
    if out_dir:
        return _existing(out_dir, video_id(url), ext)
    if explicit:
        return explicit if os.path.exists(explicit) else None
    return _existing(".", video_id(url), ext)


def _existing(out_dir: str, vid: str | None, ext: str) -> str | None:
    """The already-written file for this video, if there is one.

    Matched on the id rather than the whole filename because the rest of the
    name is the title slug, and knowing the title means probing — which is the
    request we are trying not to spend.
    """
    if not vid:
        return None  # couldn't read an id off the URL; fetching is the safe answer
    try:
        entries = os.listdir(out_dir)
    except OSError:
        return None
    hit = next((n for n in entries if n.endswith(f"-{vid}.{ext}")), None)
    return os.path.join(out_dir, hit) if hit else None


def _write(path: str, doc: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc if doc.endswith("\n") else doc + "\n")


def main(argv: list[str] | None = None) -> int:
    # prog= because the installed console script puts its full path in argv[0],
    # so --help would otherwise open with a line of C:\...\Scripts\transkrp.
    ap = argparse.ArgumentParser(prog="transkrp", description="Fetch a YouTube transcript.")
    ap.add_argument("url", nargs="+", help="video URLs; a playlist or channel URL expands")
    ap.add_argument("-o", "--out", help="output file, or a directory for several videos; "
                                        "'-' for stdout (default: ./<title-slug>-<id>.<ext>)")
    ap.add_argument("--lang", metavar="KEY",
                    help="force a track key (e.g. en-orig), or 'auto' for the "
                         "language the video is actually in")
    ap.add_argument("-f", "--format", choices=("md", "json", "srt", "vtt"), default="md",
                    help="output format (default md); srt and vtt emit the cleaned "
                         "cues, without the scroll-duplication a .vtt from YouTube has")
    ap.add_argument("--json", action="store_true", help="shorthand for --format json")
    ap.add_argument("--list", action="store_true", help="show available tracks and exit")
    ap.add_argument("--proxy", metavar="URL", help="route both requests through a proxy; "
                                                   "YouTube blocks datacenter IPs")
    ap.add_argument("--words", type=int, default=TARGET_WORDS, metavar="N",
                    help=f"target paragraph length (default {TARGET_WORDS})")
    ap.add_argument("--skip-existing", action="store_true",
                    help="don't refetch videos already written to the output "
                         "directory; use this to resume an interrupted run")
    ap.add_argument("--speakers", action="store_true",
                    help="work out who said what, by name, via the Claude API "
                         "(costs money; prints an estimate first)")
    ap.add_argument("--model", default=None, metavar="ID",
                    help="model for --speakers (default claude-opus-5)")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="skip the cost confirmation for --speakers")
    ap.add_argument("--playlist", action="store_true",
                    help="for a URL that names a video inside a playlist, take "
                         "the whole playlist rather than just that video")
    ap.add_argument("--force", action="store_true",
                    help="refetch even what --skip-existing would skip; use when "
                         "a video's captions have been corrected")
    ap.add_argument("--cookies", metavar="BROWSER|FILE",
                    help="browser to read cookies from (e.g. firefox, "
                         "'chrome:Profile 1') or a cookies.txt path; needed for "
                         "age-restricted and sign-in-required videos")
    ap.add_argument("--version", action="version", version=f"transkrp {_version()}")
    args = ap.parse_args(argv)

    # A redirected stdout defaults to the locale encoding, which on Windows is
    # cp1252 — and a Japanese title then dies on UnicodeEncodeError halfway
    # through a document whose whole point is that its typography is correct.
    # newline="\n" for the same reason: print() would otherwise translate to CRLF
    # on Windows, so `-o -` and `-o file` disagree about the same document.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")

    try:
        urls = [v for u in args.url
                for v in expand(u, args.proxy, args.cookies, args.playlist)]
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Say which reading of an ambiguous URL was used. Both are defensible and
    # the tool cannot know which was meant, so silently picking one is how a
    # person asks for 40 videos and gets 1 without ever finding out why.
    if not args.playlist:
        for u in args.url:
            if is_ambiguous(u):
                print(f"note: {u.split('&list=')[0]}...&list=... names one video "
                      f"inside a playlist; fetching just that video. "
                      f"--playlist takes the whole list.", file=sys.stderr)

    if args.list:
        return max((_list(u, args.proxy, args.cookies) for u in urls), default=0)

    # --json is the old spelling; --format is the general one.
    fmt = "json" if args.json else args.format
    ext = fmt
    to_stdout = args.out == "-"
    # One video keeps the old contract: -o is the file. Several need somewhere to
    # put them, so -o becomes the directory — silently overwriting one file with
    # the next twelve would be worse than refusing.
    #
    # A trailing slash also means a directory, whatever the arity. Without that,
    # `-o notes/` with a single video wrote a *file* called "notes", and the run
    # that added a second video then couldn't create the directory.
    wants_dir = bool(args.out) and (args.out.endswith(("/", "\\")) or os.path.isdir(args.out))
    into_dir = not to_stdout and (len(urls) > 1 or wants_dir)
    out_dir = (args.out or ".") if into_dir else None

    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            print(f"error: cannot use {out_dir!r} as an output directory: {e.strerror}",
                  file=sys.stderr)
            return 1

    # One confirmation and one running total for the whole run, not per video.
    spent = {"asked": False, "declined": False, "usd": 0.0}
    docs, failed, skipped, done, fetched = [], 0, 0, 0, False
    for url in urls:
        if args.skip_existing and not args.force and not to_stdout:
            have = _already_written(url, out_dir, args.out, ext)
            if have:
                print(f"have {os.path.basename(have)}", file=sys.stderr)
                skipped += 1
                continue

        if fetched:
            # Caption pulls are rate-limited per IP at a few hundred an hour. A
            # playlist is exactly the shape of traffic that trips it. Pace the
            # fetches, not the skips — a resume shouldn't crawl past work it
            # isn't doing.
            time.sleep(1)
        fetched = True
        try:
            t = transcript(url, args.lang, args.proxy, args.words, args.cookies,
                           segments_too=fmt in ("srt", "vtt"))
        except RateLimited as e:
            # Every remaining video will fail the same way, and asking makes the
            # block worse. Stop and say how to pick up where this left off.
            print(f"error: {url}: {e}", file=sys.stderr)
            failed += 1
            if len(urls) > 1:
                print(f"stopping with {len(urls) - done - skipped - failed} of "
                      f"{len(urls)} not fetched. Resume later with --skip-existing.",
                      file=sys.stderr)
            break
        except LookupError as e:
            print(f"error: {url}: {e}", file=sys.stderr)
            failed += 1
            continue
        done += 1

        if args.speakers:
            _attribute(t, args, spent)

        if to_stdout:
            # Keep JSON as objects: several documents concatenated are not JSON,
            # so they have to be assembled into one array at the end.
            docs.append(t if fmt == "json" else render(t, fmt))
            continue

        doc = render(t, fmt)
        name = f"{slug(t['title'], t['video_id'])}.{ext}"
        out = os.path.join(out_dir, name) if out_dir else (args.out or name)
        try:
            _write(out, doc)
        except OSError as e:
            print(f"error: cannot write {out!r}: {e.strerror}", file=sys.stderr)
            failed += 1
            continue
        # Count the unit the file actually contains: a .srt has cues, not
        # paragraphs, and reporting 99 paragraphs for a 1166-cue file is a lie.
        n, unit = ((len(t["segments"]), "cues") if fmt in ("srt", "vtt")
                   else (len(t["paragraphs"]), "paragraphs"))
        print(f"{t['title']}\n  {t['lang']} ({t['source']})"
              f"{' [machine-translated]' if t['translated'] else ''}"
              f"\n  wrote {out} ({n} {unit})", file=sys.stderr)

    if to_stdout and docs:
        print(_stdout_doc(docs, fmt == "json"))
    if len(urls) > 1:
        parts = [f"{done} written"]
        if skipped:
            parts.append(f"{skipped} already had")
        if failed:
            parts.append(f"{failed} failed")
        print(f"{', '.join(parts)} of {len(urls)}", file=sys.stderr)
    if spent["usd"]:
        print(f"speaker attribution cost roughly ${spent['usd']:.2f}", file=sys.stderr)
    return 1 if failed else 0


def _attribute(t: dict, args, spent: dict) -> None:
    """Add speaker labels, announcing the cost before the first one is spent.

    A tool that quietly bills an API account is a tool people stop trusting, so
    the estimate is printed and — unless --yes — confirmed. Confirmation happens
    once per run, not once per video: a 40-video playlist shouldn't ask 40 times.
    """
    import speakers

    n_words = len(t.get("text", "").split())
    cost = speakers.estimate_usd(n_words, args.model or speakers.DEFAULT_MODEL)
    if not spent["asked"]:
        spent["asked"] = True
        shown = f"~${cost:.2f} for this video" if cost else "an unknown amount"
        print(f"--speakers calls the Claude API and costs money ({shown}"
              f"{', more for the rest of the run' if not args.yes else ''}).",
              file=sys.stderr)
        if not args.yes and not _confirm():
            spent["declined"] = True
    if spent["declined"]:
        return

    try:
        result = speakers.attribute(t, args.model or speakers.DEFAULT_MODEL)
    except speakers.NotAvailable as e:
        print(f"  speakers: {e}", file=sys.stderr)
        spent["declined"] = True  # it will fail the same way for every video
        return
    except LookupError as e:
        print(f"  speakers: {e}", file=sys.stderr)
        return

    speakers.apply(t, result)
    if cost:
        spent["usd"] += cost
    print(f"  speakers: {', '.join(result['speakers']) or 'none identified'}"
          f" ({result['attributed']} of {len(t['paragraphs'])} paragraphs"
          f"{f', {result['unattributed']} unattributed' if result['unattributed'] else ''})",
          file=sys.stderr)


def _confirm() -> bool:
    """Ask once. A non-tty (piped, cron) declines rather than blocking forever."""
    if not sys.stdin or not sys.stdin.isatty():
        print("  not a terminal - skipping speaker attribution. Pass --yes to run it.",
              file=sys.stderr)
        return False
    try:
        return input("  proceed? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _stdout_doc(docs: list, as_json: bool) -> str:
    """One document stays one document; several become an array or are stacked.

    A lone transcript piped to jq must not arrive wrapped in a 1-element array.
    """
    if not as_json:
        return "\n\n".join(docs)
    return json.dumps(docs[0] if len(docs) == 1 else docs, indent=2, ensure_ascii=False)


def _list(url: str, proxy: str | None = None, cookies: str | None = None) -> int:
    try:
        info = probe(url, proxy, cookies)
    except LookupError as e:
        print(f"error: {url}: {e}", file=sys.stderr)
        return 1
    auto = sorted(info.get("automatic_captions") or {})
    print(info.get("title", ""))
    print(f"  language: {info.get('language') or 'unknown'}")
    print(f"  manual:   {', '.join(sorted(info.get('subtitles') or {})) or '(none)'}")
    print(f"  auto(en): {', '.join(k for k in auto if k.startswith('en')) or '(none)'}"
          f"  [+{len(auto)} total]")
    try:
        source, key, tr = pick_track(info)
    except LookupError as e:
        # Listing succeeded; there is just nothing this tool would pick. That is
        # an answer, not a failure — it is the question --list was asked.
        print(f"  -> (none): {e}")
        return 0
    print(f"  -> {key} ({source}){' [machine-translated]' if tr else ''}")
    return 0


def cli() -> int:
    """Entry point for the installed `transkrp` command.

    Exists so the console script gets the Ctrl-C contract too. Pointing the
    script at main() would mean a KeyboardInterrupt traceback for the installed
    command and a clean message for `python transkrp.py` — same program,
    different manners.
    """
    try:
        return main()
    except KeyboardInterrupt:
        # Ctrl-C during a playlist run is a decision, not a crash.
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(cli())
