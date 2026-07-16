"""Fetch a YouTube transcript as timestamped paragraphs.

CLI:
    python transkrp.py URL [-o OUT] [--lang KEY] [--json] [--list]

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
import re
import sys
import urllib.request

import yt_dlp

GAP_BREAK_MS = 2000  # a silence this long reads as a topic break, not a breath
TARGET_WORDS = 110
MIN_WORDS = 25  # don't let a pause strand a two-word paragraph


class _Silent:  # yt-dlp logs errors itself; we raise them instead
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def probe(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "logger": _Silent()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:  # unavailable, private, bad URL
        raise LookupError(str(e).replace("ERROR: ", "").strip()) from e


def pick_track(info: dict, want: str | None = None) -> tuple[str, str, bool]:
    """Return (source, key, translated). Manual wins; it needs no repair."""
    manual, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}

    if want:
        if want in manual:
            return "manual", want, False
        if want in auto:
            return "auto", want, _translated(info)
        raise LookupError(f"no caption track {want!r} (see --list)")

    for source, tracks in (("manual", manual), ("auto", auto)):
        # Keys aren't always "en": there's en-orig (original spoken track),
        # en-US/en-GB, and multi-track videos expose en-<trackid>.
        english = [k for k in tracks if k.split("-", 1)[0].lower() == "en"]
        if english:
            key = next((k for k in ("en", "en-orig", "en-US", "en-GB") if k in english), english[0])
            return source, key, _translated(info) if source == "auto" else False

    raise LookupError("no English captions (manual or auto)")


def _clean(text: str) -> str:
    """Normalise caption typography.

    Captions carry U+2011 non-breaking hyphens and U+00A0 spaces that look
    ordinary but aren't: downstream, "real<U+2011>time" won't match a search for
    "real-time" and tokenises differently. Fix at the source.
    """
    text = text.replace("‑", "-").replace("‐", "-")
    text = text.replace(" ", " ").replace("​", "")
    return text


def _translated(info: dict) -> bool:
    # YouTube auto-translates its ASR into ~150 languages and lists them all
    # under automatic_captions. An "en" entry on a non-English video is a
    # machine translation of a machine transcription.
    lang = (info.get("language") or "").lower()
    return bool(lang) and lang.split("-", 1)[0] != "en"


def segments(info: dict, source: str, key: str) -> list[tuple[int, int, str]]:
    fmts = info["subtitles" if source == "manual" else "automatic_captions"][key]
    json3 = next((f for f in fmts if f.get("ext") == "json3"), None)
    if not json3:
        raise LookupError(f"track {key!r} has no json3 format")
    with urllib.request.urlopen(json3["url"]) as r:
        events = json.loads(r.read().decode("utf-8")).get("events", [])

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


def paragraphs(segs: list[tuple[int, int, str]], punctuated: bool | None = None) -> list[tuple[int, int, str]]:
    """Group segments into (start_ms, turn, text).

    Breaks at speaker changes first, then long silences, then length. With
    punctuation we can wait for a sentence end, so the cap is loose; raw ASR has
    none, so the cap alone decides — a loose one there gives 160-word walls.
    """
    if punctuated is None:
        punctuated = is_punctuated(segs)
    cap = TARGET_WORDS * 2 if punctuated else TARGET_WORDS
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
        if punctuated and words >= TARGET_WORDS and re.search(r"[.!?][\"')\]]?\s*$", text):
            flush()
        elif words >= cap:
            flush()

    flush()
    # A paragraph of nothing but "[music]" or "[applause]" is not a turn — the
    # tag is worth keeping inline, but not as an entry of its own.
    return [p for p in out if not re.fullmatch(r"(\[[^\]]*\]|\s)+", p[2])]


def transcript(url: str, lang: str | None = None) -> dict:
    """Fetch a transcript. The one call another system needs.

    Returns a JSON-safe dict:
        title, video_id, url, source ("manual"|"auto"), lang, translated,
        duration_ms, paragraphs: [{start_ms, timestamp, text}], text

    Raises LookupError if the video is unavailable or has no English captions.
    """
    info = probe(url)
    source, key, translated = pick_track(info, lang)
    segs = segments(info, source, key)
    if not segs:
        raise LookupError(f"caption track {key!r} was empty")
    # "auto" does not mean "raw": YouTube's newer ASR emits punctuation and >>
    # speaker markers, while some manual tracks are unedited dumps that don't.
    # Detect it; don't infer it from the track's source.
    punctuated = is_punctuated(segs)
    paras = paragraphs(segs, punctuated)
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
        "duration_ms": segs[-1][1],
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch a YouTube transcript.")
    ap.add_argument("url")
    ap.add_argument("-o", "--out", help="output path; '-' for stdout (default: <video_id>.<ext>)")
    ap.add_argument("--lang", metavar="KEY", help="force a track key (e.g. en-orig)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    ap.add_argument("--list", action="store_true", help="show available tracks and exit")
    args = ap.parse_args()

    try:
        if args.list:
            return _list(args.url)
        t = transcript(args.url, args.lang)
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    doc = json.dumps(t, indent=2, ensure_ascii=False) if args.json else to_markdown(t)

    if args.out == "-":
        # Piping to another tool: document on stdout, nothing else.
        print(doc)
        return 0

    out = args.out or f"{slug(t['title'], t['video_id'])}.{'json' if args.json else 'md'}"
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc + ("" if doc.endswith("\n") else "\n"))
    print(f"{t['title']}\n  {t['lang']} ({t['source']})"
          f"{' [machine-translated]' if t['translated'] else ''}"
          f"\n  wrote {out} ({len(t['paragraphs'])} paragraphs)", file=sys.stderr)
    return 0


def _list(url: str) -> int:
    info = probe(url)
    auto = sorted(info.get("automatic_captions") or {})
    print(info.get("title", ""))
    print(f"  language: {info.get('language') or 'unknown'}")
    print(f"  manual:   {', '.join(sorted(info.get('subtitles') or {})) or '(none)'}")
    print(f"  auto(en): {', '.join(k for k in auto if k.startswith('en')) or '(none)'}"
          f"  [+{len(auto)} total]")
    source, key, tr = pick_track(info)
    print(f"  -> {key} ({source}){' [machine-translated]' if tr else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
