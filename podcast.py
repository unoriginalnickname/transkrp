"""Transcribe a podcast episode, for the common case where nobody published one.

YouTube hands over a transcript somebody else's ASR already made. Podcasts
mostly do not, so this path has to do the speech recognition itself — the only
place in the project that decodes audio.

The route, and why each step is the step:

1. **A directory link is never the source.** yt-dlp refuses Amazon Music outright
   (`[DRM] The requested site is known to use DRM protection`), and it is right
   to. Amazon and Spotify episode pages are JavaScript shells — fetched here,
   Amazon's returns 11KB with no `<title>` and no `og:title` at all — so there is
   nothing to scrape even before the DRM question. They are storefronts over a
   feed, not the feed.

2. **The feed is the source, and the iTunes Search API finds it.** No key, no
   auth, no scraping: `?term=<show>&entity=podcast` returns `feedUrl`. An Apple
   Podcasts link needs even less — the numeric id is already in the URL, so
   `lookup?id=` resolves it exactly with no guessing.

3. **Check for a published transcript first.** Podcasting 2.0 defines
   `<podcast:transcript>`, and when a show ships one it is a real transcript,
   free and better than anything ASR produces. Most don't — the feed this was
   built against declares the namespace and uses the element zero times — but
   checking costs one regex and skipping it would mean burning CPU to
   reconstruct, badly, something already sitting in the feed.

4. **Otherwise the `<enclosure>` MP3 is public and undrmed**, and whisper runs on
   it. The output is reflowed through `transkrp.paragraphs()`, because whisper
   emits ~15-word utterances and the corpus format is ~110-word timestamped
   paragraphs. Same shape as every other document here, so the graph and the
   speaker attribution work on podcasts without knowing they are podcasts.

**The output says it is ASR.** `source: whisper`, the model that made it, and a
note that names in particular need checking against the audio — because `small`
handles conversational English well and proper nouns badly, which is the exact
failure mode a corpus of people cares about. See ADR 0015.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SEARCH = "https://itunes.apple.com/search"
LOOKUP = "https://itunes.apple.com/lookup"
TIMEOUT = 30
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# `small` is the default after measuring: 41 minutes of conversational English in
# 4.5 minutes on CPU, int8. `tiny` and `base` degrade proper nouns further, which
# is the thing already worst about this path; `medium` and up want a GPU to be
# tolerable. Overridable with --whisper-model for anyone who has one.
MODEL = "small"

# Podcasting 2.0. The namespace is widely declared and the element rarely used.
PODCAST_NS = "https://podcastindex.org/namespace/1.0"

# Storefronts. Recognised so the error can say something useful, not so they can
# be fetched — none of them serves the audio, and two of them serve no metadata.
_STOREFRONT = re.compile(
    r"(?:music|open|podcasters)\.(?:amazon|spotify)\.|"
    r"open\.spotify\.com|music\.amazon\.|podcasts\.google\.|"
    r"pca\.st|overcast\.fm|castbox\.fm|iheart\.com", re.I)
_APPLE = re.compile(r"podcasts\.apple\.com/.*?/id(\d+)", re.I)
# A feed, by shape: these are conventions rather than a standard, so the parse is
# what actually decides — this only routes.
_FEEDISH = re.compile(r"\.(?:xml|rss)(?:$|\?)|/(?:feed|rss)(?:/|$|\?)|^feeds?\.", re.I)


class NotFound(LookupError):
    """No feed, no episode, or nothing to transcribe. A LookupError like the rest."""


# How an expanded feed names one of its episodes: `podcast:<feed>#<episode id>`.
# A pseudo-scheme rather than the episode's page URL, because a podbean or
# libsyn episode page is not resolvable back to the feed it came from — it would
# have to be searched for by name, and the wrong show has a similar name often
# enough. This round-trips exactly, and yt-dlp never sees it.
REF = "podcast:"


def ref(feed: str, episode_id: str) -> str:
    return f"{REF}{feed}#{episode_id}"


def _parse_ref(target: str) -> tuple[str, str | None]:
    if not target.startswith(REF):
        return target, None
    body = target[len(REF):]
    feed, _, ep_id = body.partition("#")
    return feed, ep_id or None


def episode_id(target: str) -> str | None:
    """The id out of a reference, for --skip-existing to match on a filename."""
    return _parse_ref(target)[1] if target.startswith(REF) else None


def _get(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url: str) -> dict:
    try:
        return json.loads(_get(url))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise NotFound(f"could not reach the iTunes directory: {e}") from e


def is_podcast(target: str) -> bool:
    """Whether this is ours rather than yt-dlp's.

    A bare string that isn't a URL counts: `transkrp "Some Show"` has no other
    plausible meaning, and making people learn a flag to say "this is a podcast"
    is the opposite of the point.
    """
    if not target or target.startswith("-"):
        return False
    if target.startswith(REF):
        return True
    if not re.match(r"https?://", target, re.I):
        return True
    return bool(_STOREFRONT.search(target) or _APPLE.search(target)
                or _FEEDISH.search(urllib.parse.urlparse(target).path
                                   or target))


def search(term: str, limit: int = 5) -> list[dict]:
    """Shows matching a name, best first. [] when nothing matches."""
    q = urllib.parse.urlencode({"term": term, "entity": "podcast",
                                "limit": max(1, min(limit, 25))})
    body = _json(f"{SEARCH}?{q}")
    return [{"show": r.get("collectionName") or "",
             "feed": r.get("feedUrl") or "",
             "episodes": r.get("trackCount") or 0}
            for r in body.get("results") or [] if r.get("feedUrl")]


def feed_url(target: str) -> str:
    """Resolve anything a person might paste into the URL of an RSS feed."""
    target, _ = _parse_ref(target)
    if _FEEDISH.search(urllib.parse.urlparse(target).path or "") and \
            re.match(r"https?://", target, re.I):
        return target

    if m := _APPLE.search(target):
        # The id is in the URL already, so this is a lookup rather than a search:
        # no fuzzy matching, no wrong show with a similar name.
        results = _json(f"{LOOKUP}?id={m.group(1)}&entity=podcast").get("results") or []
        for r in results:
            if r.get("feedUrl"):
                return r["feedUrl"]
        raise NotFound(f"Apple Podcasts id {m.group(1)} has no feed in the directory")

    if _STOREFRONT.search(target):
        # Deliberately not scraped. The page is a JavaScript shell — Amazon's is
        # 11KB with no title element — so there is no metadata to recover, and
        # the audio is behind DRM regardless. The show's name is the way in.
        raise NotFound(
            f"{urllib.parse.urlparse(target).netloc} is a storefront, not a feed: "
            f"the page carries no episode metadata and its audio is DRM'd. "
            f"Pass the show's name instead and this will find the real feed "
            f"(e.g. transkrp \"The Valued Cultures Podcast\").")

    hits = search(target, limit=5)
    if not hits:
        raise NotFound(f"no podcast found matching {target!r}")
    return hits[0]["feed"]


def _text(node: ET.Element | None) -> str:
    return html.unescape((node.text or "").strip()) if node is not None else ""


def _seconds(value: str) -> int:
    """itunes:duration is seconds, or H:MM:SS, or MM:SS, depending on the host."""
    value = (value or "").strip()
    if value.isdigit():
        return int(value)
    parts = value.split(":")
    try:
        nums = [int(float(p)) for p in parts]
    except ValueError:
        return 0
    total = 0
    for n in nums:
        total = total * 60 + n
    return total if len(nums) <= 3 else 0


def episodes(feed: str) -> dict:
    """Parse a feed into a show and its episodes, newest first (feed order)."""
    try:
        raw = _get(feed)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NotFound(f"could not fetch the feed {feed}: {e}") from e
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise NotFound(f"{feed} is not a valid RSS feed: {e}") from e

    channel = root.find("channel")
    if channel is None:
        raise NotFound(f"{feed} has no <channel>; it may not be a podcast feed")

    out = []
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        audio = (enclosure.get("url") or "") if enclosure is not None else ""
        # A published transcript beats anything produced here, so it is picked up
        # even though most feeds have none.
        published_transcript = ""
        for node in item.findall(f"{{{PODCAST_NS}}}transcript"):
            if node.get("url"):
                published_transcript = node.get("url")
                break
        guid = _text(item.find("guid")) or audio or _text(item.find("title"))
        out.append({
            "title": _text(item.find("title")),
            "audio": audio,
            "page": _text(item.find("link")),
            "published": _text(item.find("pubDate")),
            "description": re.sub(r"<[^>]+>", " ",
                                  _text(item.find("description"))).strip(),
            "duration_s": _seconds(_text(
                item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration"))),
            "transcript_url": published_transcript,
            # Stable, short, and derived from the feed's own identity, so the
            # same episode gets the same filename on every run. Eleven chars to
            # sit alongside YouTube ids without looking foreign.
            "id": hashlib.sha1(guid.encode("utf-8")).hexdigest()[:11],
        })
    return {"show": _text(channel.find("title")),
            "feed": feed,
            "link": _text(channel.find("link")),
            "episodes": out}


def pick(eps: list[dict], hint: str | None) -> dict:
    """The episode a person meant, by title. Newest when they didn't say."""
    if not eps:
        raise NotFound("the feed has no episodes with audio")
    if not hint:
        return eps[0]
    lowered = hint.lower()
    exact = [e for e in eps if lowered in (e["title"] or "").lower()]
    if len(exact) == 1:
        return exact[0]
    pool = exact or eps
    best = max(pool, key=lambda e: difflib.SequenceMatcher(
        None, lowered, (e["title"] or "").lower()).ratio())
    score = difflib.SequenceMatcher(
        None, lowered, (best["title"] or "").lower()).ratio()
    if score < 0.35 and not exact:
        raise NotFound(
            f"no episode matching {hint!r}. Closest was {best['title']!r}. "
            f"--list shows what the feed has.")
    return best


def _download(url: str, dest: str) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    size = 0
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
            size += len(chunk)
    return size


def transcribe(audio_url: str, model: str = MODEL,
               progress=None) -> tuple[list[tuple[int, int, str]], str]:
    """Speech recognition on an episode. Returns cues and the model used.

    The two non-default whisper settings are not tuning, they are the difference
    between a transcript and a loop: `condition_on_previous_text` defaults to
    True, which lets one hallucinated phrase feed itself and repeat for minutes,
    and `vad_filter` defaults to False, which invites exactly that during silence.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise NotFound(
            "podcasts need speech recognition, which isn't installed. "
            "pip install \".[podcast]\"") from e

    fd, path = tempfile.mkstemp(suffix=".audio")
    os.close(fd)
    try:
        if progress:
            progress(f"downloading {audio_url.rsplit('/', 1)[-1]}")
        _download(audio_url, path)
        if progress:
            progress(f"transcribing with whisper {model} (cpu, int8)")
        # int8 on CPU: this is the configuration that made the runtime bearable
        # without a GPU, and the accuracy cost against float32 was not audible in
        # the prose. See ADR 0015.
        whisper = WhisperModel(model, device="cpu", compute_type="int8")
        segments, _info = whisper.transcribe(
            path, vad_filter=True, condition_on_previous_text=False)
        cues = []
        for s in segments:
            text = (s.text or "").strip()
            if text:
                cues.append((int(s.start * 1000), int(s.end * 1000), text))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if not cues:
        raise NotFound("speech recognition produced nothing; the audio may be silent")
    return cues, f"faster-whisper {model} (int8, CPU)"


def _published(url: str) -> list[tuple[int, int, str]]:
    """Cues from a feed's own transcript file. VTT or SRT; both are timed text."""
    body = _get(url).decode("utf-8", "replace")
    cues = []
    pattern = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*"
        r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})(.*?)(?=\n\s*\n|\Z)", re.S)
    for m in pattern.finditer(body):
        h1, m1, s1, ms1, h2, m2, s2, ms2, text = m.groups()
        start = ((int(h1) * 60 + int(m1)) * 60 + int(s1)) * 1000 + int(ms1)
        end = ((int(h2) * 60 + int(m2)) * 60 + int(s2)) * 1000 + int(ms2)
        clean = re.sub(r"<[^>]+>", "", text).strip()
        if clean:
            cues.append((start, end, clean))
    return cues


def transcript(target: str, episode: str | None = None, model: str = MODEL,
               target_words: int = 110, whole_feed: bool = False,
               progress=None) -> dict:
    """A podcast episode in the same shape every other document here has.

    `transkrp.transcript` returns this for YouTube; anything downstream — the
    markdown, the graph, speaker attribution — works on either without caring.
    """
    import transkrp  # circular by nature: this fills in a transkrp code path

    _, ref_id = _parse_ref(target)
    feed = feed_url(target)
    show = episodes(feed)
    playable = [e for e in show["episodes"] if e["audio"] or e["transcript_url"]]
    if ref_id:
        # Came from an expanded feed, so the episode is already decided; matching
        # by title here would reintroduce the ambiguity the id exists to avoid.
        ep = next((e for e in playable if e["id"] == ref_id), None)
        if ep is None:
            raise NotFound(f"episode {ref_id} is no longer in {feed}")
    else:
        ep = pick(playable, episode)

    source, model_used = "whisper", ""
    cues: list[tuple[int, int, str]] = []
    if ep["transcript_url"]:
        # Free, exact, and somebody meant it. Only fall through to ASR if the
        # file turns out to be unusable.
        try:
            cues = _published(ep["transcript_url"])
            source = "published"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            cues = []
    if not cues:
        if not ep["audio"]:
            raise NotFound(f"{ep['title']!r} has no audio to transcribe")
        cues, model_used = transcribe(ep["audio"], model, progress)

    punctuated = transkrp.is_punctuated(cues)
    paras = transkrp.paragraphs(cues, punctuated, target_words)
    duration = ep["duration_s"] * 1000 or (cues[-1][1] if cues else 0)
    return {
        "channel": show["show"],
        "show": show["show"],
        "feed": feed,
        "upload_date": _rfc822(ep["published"]),
        "description": ep["description"],
        "chapters": [],
        "title": ep["title"],
        "video_id": ep["id"],
        # The episode page for a human; the audio is what the timestamps link to.
        "url": ep["page"] or ep["audio"],
        "audio": ep["audio"],
        "source": source,
        "model": model_used,
        "lang": "en",
        "translated": False,
        "punctuated": punctuated,
        "duration_ms": duration,
        "captions_end_ms": cues[-1][1] if cues else 0,
        "turns": len({turn for _, turn, _ in paras}),
        "paragraphs": [
            {"start_ms": ms, "timestamp": transkrp.stamp(ms), "turn": turn,
             "text": text}
            for ms, turn, text in paras
        ],
        "text": " ".join(text for _, _, text in paras),
        "segments": [{"start_ms": s, "end_ms": e, "text": t} for s, e, t in cues],
    }


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _rfc822(value: str) -> str:
    """RSS dates are RFC-822; the frontmatter is ISO, like every other document."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})", value or "")
    if not m:
        return ""
    day, mon, year = m.groups()
    month = _MONTHS.get(mon.lower())
    return f"{year}-{month:02d}-{int(day):02d}" if month else ""
