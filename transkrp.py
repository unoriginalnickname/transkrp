"""Fetch a YouTube transcript as timestamped paragraphs.

CLI:
    python transkrp.py URL... [-o OUT] [--lang KEY] [--json] [--list]

Library:
    from transkrp import transcript
    t = transcript("https://youtube.com/watch?v=...")   # -> dict, JSON-safe
    t["paragraphs"][0]["text"]

yt-dlp does the fetching and already prefers manual captions over auto ones.
This adds the two things it doesn't do: drop the scroll-duplicate events in
auto-captions, and reflow cues into readable paragraphs.
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
        raise LookupError(str(e).replace("ERROR: ", "").strip()) from e


def probe(url: str, proxy: str | None = None) -> dict:
    """Video metadata, including the index of caption tracks.

    `noplaylist` because a video shared from inside a playlist carries `&list=`,
    and yt-dlp would otherwise extract all 200 of its neighbours.
    """
    info = _extract(url, noplaylist=True, proxy=proxy)
    if info.get("_type") == "playlist":
        n = len(info.get("entries") or [])
        raise LookupError(f"that URL is a playlist ({n} videos), not a video")
    return info


# A single video shared from inside a playlist carries `&list=`, so a `v=` id
# wins: pasting YouTube's share link should fetch the video you were watching,
# not the 200 around it.
_VIDEO_URL = re.compile(r"[?&]v=[\w-]{11}|youtu\.be/[\w-]{11}|/shorts/[\w-]{11}")
_LIST_URL = re.compile(r"/playlist|/channel/|/@|/c/|/user/|[?&]list=", re.I)


def is_playlist_url(url: str) -> bool:
    """Does this URL name several videos rather than one?"""
    return not _VIDEO_URL.search(url) and bool(_LIST_URL.search(url))


def expand(url: str, proxy: str | None = None) -> list[str]:
    """Resolve one URL to video URLs; a playlist or channel becomes its entries.

    Flat extraction, so a 200-video playlist costs one request instead of 200 —
    each video is probed later anyway, and only if we get that far.
    """
    if not is_playlist_url(url):
        return [url]
    info = _extract(url, extract_flat="in_playlist", proxy=proxy)
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
    """Return (source, key, translated). Manual wins; it needs no repair."""
    manual, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}

    if want:
        if want in manual:
            return "manual", want, False
        if want in auto:
            return "auto", want, _translated(info)
        raise LookupError(f"no caption track {want!r}; manual tracks are "
                          f"{_names(manual)} (see --list for the auto ones)")

    for source, tracks in (("manual", manual), ("auto", auto)):
        # Keys aren't always "en": there's en-orig (original spoken track),
        # en-US/en-GB, and multi-track videos expose en-<trackid>.
        english = [k for k in tracks if k.split("-", 1)[0].lower() == "en"]
        if english:
            key = next((k for k in ("en", "en-orig", "en-US", "en-GB") if k in english), english[0])
            return source, key, _translated(info) if source == "auto" else False

    # Say what there *is*: on a foreign-language video the fix is --lang, and
    # "no English captions" alone doesn't tell you that one would work.
    if manual or auto:
        raise LookupError(f"no English captions; manual tracks are {_names(manual)} "
                          f"- pass --lang to pick one")
    raise LookupError("this video has no captions at all")


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


def _translated(info: dict) -> bool:
    # YouTube auto-translates its ASR into ~150 languages and lists them all
    # under automatic_captions. An "en" entry on a non-English video is a
    # machine translation of a machine transcription.
    lang = (info.get("language") or "").lower()
    return bool(lang) and lang.split("-", 1)[0] != "en"


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
                time.sleep(_backoff(attempt))
                continue
            if e.code == 429:
                raise LookupError("YouTube rate-limited the caption fetch (429) - "
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
        raise LookupError(f"track {key!r} has no json3 format")
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
        # aAppend events hold only a newline. They drive the scrolling two-line
        # display and repeat text already emitted; keeping them triples the
        # transcript. This is why naive .vtt scraping produces duplicates.
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
               target: int = TARGET_WORDS) -> dict:
    """Fetch a transcript. The one call another system needs.

    Returns a JSON-safe dict:
        title, video_id, url, source ("manual"|"auto"), lang, translated,
        duration_ms, paragraphs: [{start_ms, timestamp, turn, text}], text

    Raises LookupError if the video is unavailable or has no English captions.
    """
    info = probe(url, proxy)
    source, key, translated = pick_track(info, lang)
    segs = segments(info, source, key, proxy=proxy)
    if not segs:
        raise LookupError(f"caption track {key!r} was empty")
    # "auto" does not mean "raw": YouTube's newer ASR emits punctuation and >>
    # speaker markers, while some manual tracks are unedited dumps that don't.
    # Detect it; don't infer it from the track's source.
    punctuated = is_punctuated(segs)
    paras = paragraphs(segs, punctuated, target)
    # The last cue's end is where the *captions* stop, which on a video that ends
    # in music is minutes short of the runtime. Prefer what the metadata says.
    duration = info.get("duration")
    return {
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
    }


def slug(title: str, video_id: str, limit: int = 60) -> str:
    """A findable filename: title slug + id.

    The slug makes it browsable in a directory of knowledge files; the id keeps
    it unique and traceable back to the video.
    """
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:limit].strip("-")
    return f"{s}-{video_id}" if s else (video_id or "transcript")


def to_markdown(t: dict) -> str:
    head = ["---", f"title: {t['title']}", f"url: {t['url']}",
            f"source: {t['source']}", f"lang: {t['lang']}",
            f"punctuated: {str(t['punctuated']).lower()}"]
    if t["translated"]:
        head.append("translated: true  # machine translation of machine transcription")
    if t["turns"] > 1:
        head.append(f"turns: {t['turns']}  # speaker changes; who is speaking is not marked")
    if not t["punctuated"]:
        head.append("note: unpunctuated speech recognition - no sentence breaks or speaker labels")
    head.append("---")

    # Mark a speaker change with ">>" — the standard transcript convention, and
    # greppable. A horizontal rule per turn would mean 500+ rules in a long
    # interview, which is heavier than the dialogue it annotates. The dict's
    # `text` stays clean; the marker is presentation.
    lines, prev_turn = [], None
    for p in t["paragraphs"]:
        new_turn = prev_turn is not None and p["turn"] != prev_turn
        marker = ">> " if new_turn else ""
        lines.append(f"[{p['timestamp']}] {marker}{p['text']}")
        prev_turn = p["turn"]
    return "\n".join(head) + f"\n\n# {t['title']}\n\n" + "\n\n".join(lines) + "\n"


def stamp(ms: int) -> str:
    t = ms // 1000
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t % 3600 // 60:02d}:{t % 60:02d}"


def render(t: dict, as_json: bool) -> str:
    return json.dumps(t, indent=2, ensure_ascii=False) if as_json else to_markdown(t)


def _write(path: str, doc: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc if doc.endswith("\n") else doc + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch a YouTube transcript.")
    ap.add_argument("url", nargs="+", help="video URLs; a playlist or channel URL expands")
    ap.add_argument("-o", "--out", help="output file, or a directory for several videos; "
                                        "'-' for stdout (default: ./<title-slug>-<id>.<ext>)")
    ap.add_argument("--lang", metavar="KEY", help="force a track key (e.g. en-orig)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    ap.add_argument("--list", action="store_true", help="show available tracks and exit")
    ap.add_argument("--proxy", metavar="URL", help="route both requests through a proxy; "
                                                   "YouTube blocks datacenter IPs")
    ap.add_argument("--words", type=int, default=TARGET_WORDS, metavar="N",
                    help=f"target paragraph length (default {TARGET_WORDS})")
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
        urls = [v for u in args.url for v in expand(u, args.proxy)]
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.list:
        return max((_list(u, args.proxy) for u in urls), default=0)

    ext = "json" if args.json else "md"
    to_stdout = args.out == "-"
    # One video keeps the old contract: -o is the file. Several need somewhere to
    # put them, so -o becomes the directory — silently overwriting one file with
    # the next twelve would be worse than refusing.
    into_dir = not to_stdout and (len(urls) > 1 or (args.out and os.path.isdir(args.out)))
    out_dir = (args.out or ".") if into_dir else None

    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            print(f"error: cannot use {out_dir!r} as an output directory: {e.strerror}",
                  file=sys.stderr)
            return 1

    docs, failed = [], 0
    for i, url in enumerate(urls):
        if i:
            # Caption pulls are rate-limited per IP at a few hundred an hour. A
            # playlist is exactly the shape of traffic that trips it.
            time.sleep(1)
        try:
            t = transcript(url, args.lang, args.proxy, args.words)
        except LookupError as e:
            print(f"error: {url}: {e}", file=sys.stderr)
            failed += 1
            continue

        if to_stdout:
            # Keep JSON as objects: several documents concatenated are not JSON,
            # so they have to be assembled into one array at the end.
            docs.append(t if args.json else to_markdown(t))
            continue

        doc = render(t, args.json)
        name = f"{slug(t['title'], t['video_id'])}.{ext}"
        out = os.path.join(out_dir, name) if out_dir else (args.out or name)
        try:
            _write(out, doc)
        except OSError as e:
            print(f"error: cannot write {out!r}: {e.strerror}", file=sys.stderr)
            failed += 1
            continue
        print(f"{t['title']}\n  {t['lang']} ({t['source']})"
              f"{' [machine-translated]' if t['translated'] else ''}"
              f"\n  wrote {out} ({len(t['paragraphs'])} paragraphs)", file=sys.stderr)

    if to_stdout and docs:
        print(_stdout_doc(docs, args.json))
    return 1 if failed else 0


def _stdout_doc(docs: list, as_json: bool) -> str:
    """One document stays one document; several become an array or are stacked.

    A lone transcript piped to jq must not arrive wrapped in a 1-element array.
    """
    if not as_json:
        return "\n\n".join(docs)
    return json.dumps(docs[0] if len(docs) == 1 else docs, indent=2, ensure_ascii=False)


def _list(url: str, proxy: str | None = None) -> int:
    try:
        info = probe(url, proxy)
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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # Ctrl-C during a playlist run is a decision, not a crash.
        print("\ninterrupted", file=sys.stderr)
        raise SystemExit(130)
