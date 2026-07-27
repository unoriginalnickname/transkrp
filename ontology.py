"""A small domain model for speaker attribution, and the checks it enables.

Borrowed from Frank Coyle's argument that a probabilistic model needs a formal
layer beside it: *Pydantic at the door, ontology at the ledger*. `speakers.py`
already had the door — it checks that the reply is JSON and that paragraph
numbers land in range. That is shape. This is meaning.

The three errors Coyle uses to motivate it all have exact analogues here:

| His example | Here |
|---|---|
| an order status of "probably shipped" | a speaker name the video's metadata has never heard of |
| a payout sent to the support desk, not the buyer | the host and the guest turning out to be the same person |
| a second refund on the same order | one person arriving under three spellings, becoming three people |

That last one is why this module exists at all rather than being a nicety. The
corpus is for cross-referencing people across many playlists, and "Tom O'Neill",
"Tom O'Neil" and "O'Neill" are three separate individuals to any graph that
joins on name. Speech recognition guarantees those variants; the description
holds the correct spelling. Canonicalising against it is the difference between
a map of people and a map of spellings.

Nothing here calls a model. It is deterministic, offline, and testable, which is
the point: the check on the probabilistic step must not itself be probabilistic.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from nameparser import HumanName
from rapidfuzz import fuzz

# Two people is the shape of an interview. More is normal when clips and
# archival audio are played, so this flags rather than rejects — the count that
# means "drift" and the count that means "documentary" look identical from here.
EXPECTED_SPEAKERS = 4

# Capitalised runs that are titles, orgs or sentence openers rather than people.
_NOT_A_NAME = {
    "the", "this", "that", "these", "those", "his", "her", "their", "our",
    "and", "but", "for", "with", "from", "into", "about", "after", "before",
    "in", "on", "at", "by", "of", "to", "a", "an", "is", "was", "he", "she",
    "it", "they", "we", "you", "i", "if", "so", "as", "who", "what", "when",
    "why", "how", "new", "old", "first", "last", "next", "one", "two",
    "subscribe", "watch", "follow", "listen", "support", "episode", "part",
    "chapter", "intro", "outro", "links", "sponsor", "patreon", "twitter",
    "instagram", "youtube", "podcast", "video", "channel", "show", "series",
    "university", "college", "institute", "center", "centre", "company",
    "corporation", "foundation", "society", "association", "department",
}


def tidy_name(name: str) -> str:
    """Strip what a sentence leaves stuck to a name.

    Extraction pulls names out of running speech, so they arrive wearing the
    grammar around them: "Magnus Carlsen's" came back as a person from a real
    transcript. A possessive is not part of anyone's name, and left alone it
    becomes a second node for someone the graph already has.
    """
    name = re.sub(r"[’']s\b", "", name.strip())
    return re.sub(r"\s+", " ", name.strip(" ,.;:!?\"'()[]")).strip()


def is_full_name(name: str) -> bool:
    """Two words or more — enough to identify someone across a corpus.

    A bare given name is real information inside its own recording and a hazard
    outside it: forty videos will contain several unrelated Maxes, and merging
    them on the string would invent a person who connects things no one person
    connects. Callers keep single-name people but must not merge them globally.
    """
    return len(_fold(tidy_name(name)).split()) >= 2


def _fold(text: str) -> str:
    """A comparison key: case, accents, punctuation and spacing removed.

    "Tom O'Neill" and "Tom ONeill" fold together. "Tom O'Neil" does **not** —
    folding normalises how a name is written, not how it was misheard, and a
    dropped letter survives it. `_close` below covers that separately; keeping
    the two apart matters because an exact fold can merge people safely and a
    fuzzy match cannot.
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", text.lower())).strip()


# Below this, a one-letter difference is more likely a different name than a
# mishearing: "Tim" and "Tom" are two people, "O'Neil" and "O'Neill" are one.
_SLIP_SAFE_LENGTH = 5


def _close(a: str, b: str) -> bool:
    """Near-identical names — one transcription slip apart.

    Speech recognition drops and doubles letters constantly ("O'Neil" for
    "O'Neill"), and exact folding misses all of it, leaving a corpus with two
    nodes for one person.

    Compared **word by word**, which no single similarity score can replace:
    rapidfuzz rates "tim oneill" against "tom oneill" at 90, comfortably inside
    any threshold that also accepts the real slips — so a whole-string ratio
    fuses Tim and Tom whatever you set it to. Per word, the surnames match
    exactly and the difference lands on a three-letter given name, too short for
    a slip to be the likelier explanation. The library supplies the comparison;
    the per-word structure is what makes it safe.

    Loosening this trades a missed merge for a wrong one, and merging two real
    people is much the worse error: a split person is visible in the data, a
    fused one is not.
    """
    left, right = a.split(), b.split()
    if not left or len(left) != len(right):
        return False
    for x, y in zip(left, right):
        if x == y:
            continue
        if min(len(x), len(y)) < _SLIP_SAFE_LENGTH:
            return False
        if fuzz.ratio(x, y) < 85:
            return False
    return True


def _parts(name: str) -> tuple[str, str]:
    """(given name, family name), folded — via nameparser, not by splitting.

    Splitting on whitespace and taking the ends is wrong for any name carrying a
    title, a suffix or a particle, and those are common in exactly the material
    this reads: "Dr. Jane Smith Jr." gave first="dr", last="jr", and
    "Tom O'Neill, PhD" gave last="phd". nameparser knows about all of it,
    including "van der Berg" as one surname.
    """
    parsed = HumanName(name)
    return _fold(parsed.first), _fold(parsed.last)


def _surname(name: str) -> str:
    return _parts(name)[1] or (_fold(name).split() or [""])[-1]


def names_in(text: str) -> list[str]:
    """Person-shaped capitalised runs, in order of appearance.

    A regex, not a model — deliberately. It over-collects (organisations slip
    through) and that is the safe direction: this builds the set a claimed
    speaker is checked *against*, so a false positive costs a missed flag while
    a false negative would reject a real person.
    """
    out, seen = [], set()
    # No full stops inside a word and no newlines between them. Allowing either
    # produced real garbage on a real description: "Sixties. O'Neill" welded a
    # sentence end onto the next name, and "AlchemyAmerican \nEMAIL" spanned a
    # line break. Both then sat in the known-people set, where a bare surname
    # could resolve to one of them and become the canonical spelling.
    for run in re.findall(r"\b[A-Z][\w'’-]*(?:[ \t]+[A-Z][\w'’-]*){1,3}\b", text):
        words = run.split()
        if any(_fold(w) in _NOT_A_NAME for w in words):
            continue
        if len(_fold(run)) < 5:
            continue
        key = _fold(run)
        if key not in seen:
            seen.add(key)
            out.append(run.strip(".,;:"))
    return out


def known_people(t: dict, corpus: dict[str, str] | None = None) -> dict[str, str]:
    """Everyone this video names, plus everyone the corpus already knows.

    The channel is the host. The title and description carry the guest, spelled
    by a human rather than by speech recognition. Keyed by fold, so a mangled
    variant finds its way home.

    `corpus` is what makes cross-referencing work rather than merely look like
    it. A person introduced properly in episode 3 is often only *mentioned* in
    episode 12 — no description entry, just ASR-mangled speech — so per-video
    knowledge alone leaves them ungrounded there, flagged, and unjoinable to
    their own earlier appearance. Carrying confirmed people forward resolves
    them everywhere they speak.

    The video's own metadata still wins on a collision: it is the more
    authoritative source for the video it describes.
    """
    people: dict[str, str] = dict(corpus or {})
    own: dict[str, str] = {}

    def add(name: str) -> None:
        key = _fold(name)
        # Longest spelling wins: "Daniel Peter Sheehan" over a later "Sheehan".
        if key and (key not in own or len(name) > len(own[key])):
            own[key] = name

    if channel := (t.get("channel") or "").strip():
        add(channel)
    for name in names_in(_title_credits(t.get("title") or "")):
        add(name)
    for name in names_in(t.get("description") or ""):
        add(name)

    people.update(own)
    return people


def confirmed(t: dict, result: dict, corpus: dict[str, str] | None = None) -> dict[str, str]:
    """The people this video actually established, for the corpus to remember.

    Only speakers that were *grounded* — vouched for by the video's own metadata
    — are carried forward. An ungrounded name is exactly the kind of thing that
    should not spread: propagating a hallucinated or misheard speaker across a
    corpus would make it self-confirming, and the flag on it would disappear at
    the moment it started doing damage.
    """
    known = known_people(t, corpus)
    out: dict[str, str] = {}
    for label in result.get("labels") or []:
        if not (label and label.get("speaker")):
            continue
        name, grounded = canonicalise(label["speaker"], known)
        if grounded:
            out[_fold(name)] = name
    return out


# Where a title actually credits a person: "... — Frank Coyle, UC Berkeley",
# "(ft. Nick Cook)", "with Jane Doe".
_CREDIT = re.compile(r"(?:\bft\.?|\bfeat\.?|\bwith\b|\bw/|[—–|]|\s-\s)\s*(.+)$", re.I)


def _title_credits(title: str) -> str:
    """The crediting tail of a title, or nothing.

    Titles are sentence-cased headlines, so every word is capitalised and the
    name regex happily reports "Every American Conspiracy" as a person. Only the
    part after a credit marker is prose-like enough to trust; a title with no
    marker contributes no names at all, and the description picks up the slack.
    """
    match = _CREDIT.search(title)
    return match.group(1).strip(" )") if match else ""


def canonicalise(name: str, known: dict[str, str]) -> tuple[str, bool]:
    """Resolve a claimed name against the metadata. Returns (name, grounded).

    Exact fold first, then surname — "O'Neill" alone should resolve to the
    description's "Tom O'Neill" rather than becoming a second person. Ungrounded
    names are returned unchanged and flagged, not dropped: a guest introduced
    only in speech is real, just unverifiable from metadata.
    """
    key = _fold(name)
    if key in known:
        return known[key], True

    # A near-miss on the whole name: the ASR heard it slightly wrong.
    near = [full for k, full in known.items() if _close(k, key)]
    if len(near) == 1:
        return near[0], True

    # Same given name and same family name is the same person, whatever sits
    # between or after them: "Daniel Sheehan" is the "Daniel Peter Sheehan" of
    # the description, and "Tom O'Neill, PhD" is Tom O'Neill. A differing given
    # name still blocks it, so siblings stay apart.
    first, last = _parts(name)
    if first and last:
        matches = [full for full in known.values() if _parts(full) == (first, last)]
        if len({_fold(m) for m in matches}) == 1:
            return matches[0], True

    # Surname-only resolution applies to a *bare* surname and nothing else.
    # "O'Neill" with no given name is an abbreviation of the known person; "Tim
    # O'Neill" is a claim about a different one, and matching it to Tom on the
    # shared surname would fuse two people. A given name that disagrees is
    # evidence, not noise.
    if len(key.split()) == 1 and key:
        for match in (lambda k: _surname(k) == key,
                      lambda k: _close(_surname(k), key)):
            matches = [full for k, full in known.items() if match(k)]
            if len(matches) == 1:      # ambiguous surnames stay unresolved
                return matches[0], True
    return name, False


class Violation:
    """A constraint that the model's answer broke, in terms a human can act on."""

    def __init__(self, kind: str, detail: str, paragraphs: list[int] | None = None):
        self.kind, self.detail = kind, detail
        self.paragraphs = paragraphs or []

    def __repr__(self) -> str:  # shows up in test failures
        return f"Violation({self.kind}: {self.detail})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, Violation) and self.kind == other.kind
                and self.detail == other.detail and self.paragraphs == other.paragraphs)


def check(t: dict, result: dict,
          corpus: dict[str, str] | None = None) -> tuple[dict, list[Violation]]:
    """Validate an attribution against the video, before it reaches the document.

    Returns a corrected result and the constraints it broke. Corrections are
    conservative: spellings are canonicalised, and a name the metadata cannot
    account for is demoted to low confidence rather than deleted — being unsure
    about a real person is recoverable, erasing them is not.
    """
    known = known_people(t, corpus)
    labels = list(result.get("labels") or [])
    violations: list[Violation] = []

    # --- functional: one canonical spelling per person -------------------
    fixed_labels: list[dict | None] = []
    ungrounded: dict[str, list[int]] = {}
    # Grouped by what each spelling *resolved to*, not by how it was written —
    # the whole point is that "tom o'neil" and "Tom O'Neill" write differently.
    merged: dict[str, set[str]] = {}
    for i, label in enumerate(labels, 1):
        if not (label and label.get("speaker")):
            fixed_labels.append(label)
            continue
        claimed = label["speaker"]
        name, grounded = canonicalise(claimed, known)
        merged.setdefault(name, set()).add(claimed)
        entry = dict(label, speaker=name)
        if not grounded:
            # Coyle's "probably shipped": a value outside the domain. Kept, but
            # never presented as certain.
            entry["confidence"] = "low"
            ungrounded.setdefault(name, []).append(i)
        fixed_labels.append(entry)

    for name, paras in ungrounded.items():
        violations.append(Violation(
            "ungrounded_speaker",
            f"{name!r} is not named in the title, description or channel",
            paras))

    # A silent merge is still a change to the data, so it gets reported.
    for name, spellings in merged.items():
        if len(spellings) > 1 or (spellings and next(iter(spellings)) != name):
            if spellings != {name}:
                violations.append(Violation(
                    "spelling_variants",
                    f"{sorted(spellings)} treated as one person: {name!r}"))

    # --- the speaker list, rebuilt from what survived --------------------
    speakers, seen = [], set()
    for label in fixed_labels:
        if label and (name := label.get("speaker")) and _fold(name) not in seen:
            seen.add(_fold(name))
            speakers.append(name)

    # --- disjoint: the host cannot also be the guest ----------------------
    channel = (t.get("channel") or "").strip()
    if channel:
        guests = [s for s in speakers if _fold(s) != _fold(channel)]
        for guest in guests:
            if _surname(guest) and _surname(guest) == _surname(channel):
                violations.append(Violation(
                    "host_guest_conflict",
                    f"{guest!r} and the channel {channel!r} look like one person"))

    # --- cardinality: a plausible number of voices ------------------------
    if len(speakers) > EXPECTED_SPEAKERS:
        violations.append(Violation(
            "many_speakers",
            f"{len(speakers)} speakers ({', '.join(speakers)}) - clips and "
            f"archival audio explain this, drift also explains this"))

    # --- coverage: an attribution that attributes almost nothing ----------
    attributed = sum(1 for x in fixed_labels if x and x.get("speaker"))
    if labels and attributed / len(labels) < 0.5:
        violations.append(Violation(
            "sparse_coverage",
            f"only {attributed} of {len(labels)} paragraphs attributed"))

    corrected = dict(result, labels=fixed_labels, speakers=speakers,
                     attributed=attributed,
                     unattributed=len(fixed_labels) - attributed)
    return corrected, violations


def retry_hint(violations: list[Violation], known: dict[str, str]) -> str:
    """What to tell the model when asking it to think again.

    Coyle's loop closes here: an unreasonable result goes back with the reason,
    rather than being quietly accepted or quietly dropped.
    """
    if not violations:
        return ""
    lines = ["Your previous answer broke these constraints:"]
    lines += [f"- {v.detail}" for v in violations]
    if known:
        lines.append("")
        lines.append("People this video's metadata actually names: "
                     + ", ".join(sorted(set(known.values()))))
        lines.append("Use these spellings. If someone speaks who is not on that "
                     "list, keep them but expect to justify it.")
    return "\n".join(lines)


def summarise(violations: list[Violation]) -> str:
    """One line for the CLI. Counts by kind, so a long list stays readable."""
    if not violations:
        return ""
    counts = Counter(v.kind for v in violations)
    return ", ".join(f"{kind}×{n}" if n > 1 else kind for kind, n in counts.items())
