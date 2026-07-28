"""Drop sponsor reads, using SponsorBlock's crowd-sourced timings.

Every episode of a series opens with the same "huge thank you to our sponsor,
discount code" read. Thirty near-identical passages in a corpus is not mild
noise — repetition is what makes a passage look salient to a retrieval index, so
the one thing the episodes are *not* about gets boosted for being frequent.

Two rules shape everything here, both from [ADR 0014](docs/adr/0014-strip-sponsor-reads-on-request-and-say-so.md):

**This must never be able to fail a fetch.** A transcript is perfectly good
without the cut. A 404, a timeout, a mangled body and an unreachable host all
mean the same thing — no segments — and the transcript proceeds intact.

**What was removed travels with the document.** `strip` reports the spans it
applied so the frontmatter can say where the gaps are. An incomplete document
that says where it is incomplete is still evidence; one that doesn't is a
transcript that quietly isn't.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request

API = "https://sponsor.ajay.app/api/skipSegments"
TIMEOUT = 10  # shorter than the caption fetch: this is optional, don't stall on it

# `sponsor` only. SponsorBlock also has selfpromo, interaction, intro, outro and
# filler, but those shade into content — a creator plugging their own project is
# usually discussing the subject of the video. This category has a crisp
# definition and is the one the complaint was about.
CATEGORY = "sponsor"

# Crowd-sourced data is occasionally wrong or vandalised, and one mis-submitted
# segment can span a whole runtime. A transcript degraded to nothing is a worse
# outcome than a sponsor read left in, and the asymmetry is sharp enough to
# hard-code rather than expose as a knob.
MOST_REMOVABLE = 0.5


def _prefix(video_id: str) -> str:
    """The first four hex characters of the ID's SHA-256.

    Querying `?videoID=` would tell the API exactly what we are transcribing.
    The prefix endpoint answers for every video sharing those four characters —
    55 of them when measured — so what leaks is the prefix, not the video.
    """
    return hashlib.sha256(video_id.encode()).hexdigest()[:4]


def fetch(video_id: str, proxy: str | None = None) -> list[tuple[int, int]]:
    """Sponsor spans for a video, in milliseconds. Never raises.

    Returns [] for a video nobody has submitted segments for, which the API
    signals with a 404 — the same 404 it gives for a nonsense ID, so the two
    cannot be told apart and the common case by far is the innocent one.
    """
    if not video_id:
        return []
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(f"{API}/{_prefix(video_id)}", timeout=TIMEOUT) as r:
            body = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return []

    spans = []
    for entry in body if isinstance(body, list) else []:
        if not isinstance(entry, dict) or entry.get("videoID") != video_id:
            continue                       # a different video sharing the prefix
        for seg in entry.get("segments") or []:
            if not isinstance(seg, dict) or seg.get("category") != CATEGORY:
                continue
            pair = seg.get("segment")
            if not (isinstance(pair, list) and len(pair) == 2):
                continue
            try:
                start, end = float(pair[0]), float(pair[1])
            except (TypeError, ValueError):
                continue
            if end > start:                # the API gives seconds; everything here is ms
                spans.append((int(start * 1000), int(end * 1000)))
    return sorted(spans)


def strip(segs: list[tuple[int, int, str]],
          spans: list[tuple[int, int]]) -> tuple[list[tuple[int, int, str]],
                                                 list[tuple[int, int]]]:
    """Drop cues inside a sponsor span. Returns what survives and what was cut.

    Cutting happens at cue level and before paragraphs are built, so
    segmentation runs on what survives and no paragraph straddles a gap.

    A cue counts as inside when its *midpoint* is, which settles the cue that
    straddles a boundary without inventing a fractional-overlap threshold.

    The second return value is the spans that actually removed something — not
    the spans asked for. A span covering a silent stretch with no cues in it
    removed nothing, and the document should not claim a gap that isn't there.
    """
    if not segs or not spans:
        return segs, []

    kept, applied = [], set()
    for start, end, text in segs:
        mid = (start + end) // 2
        hit = next((s for s in spans if s[0] <= mid < s[1]), None)
        if hit is None:
            kept.append((start, end, text))
        else:
            applied.add(hit)

    # Refuse a cut that would eat the video: bad data, not a very sponsored talk.
    if len(kept) < len(segs) * (1 - MOST_REMOVABLE):
        return segs, []
    return kept, sorted(applied)
