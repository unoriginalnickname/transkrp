"""Optional: work out who said what, by name.

The caption tracks don't say. Measured on a 30-episode interview playlist, not
one carried a single `>>` speaker marker — so a corpus built for cross-
referencing people has no idea which of the two voices is talking, which is
fatal for the one thing it's for.

Three ways to fix that, and they are not close in cost:

| Approach | Correctness | Cost |
|---|---|---|
| `>>` markers in the track | exact | free, and absent from 30/30 real videos |
| **Labelling from the transcript (this)** | inferred, checkable | **~$3 for a 280,000-word corpus** |
| Audio diarization (pyannote) | measured from audio | GB of model weights, minutes per video |

This module does the middle one, and it is cheap for a reason worth
understanding: **the model returns labels, not text.** A quarter-million words go
in as input; a few thousand short labels come back. Cost is dominated by the
cheap side of the ledger — see `estimate_usd`.

It reads names off the video's own metadata rather than guessing them. The
channel is the host; the description's first sentence names the guest, spelled
correctly by a human — which matters because ASR mangles exactly those words
("Danny shean" for Daniel Sheehan, "Elon hubber" for L. Ron Hubbard). Feeding it
the description is what turns "SPEAKER_01" into a name you can cross-reference.

Generated, therefore opt-in, kept in its own field, and announced in the output
(ADR 0011). Every label carries the model's own confidence, and a paragraph it
won't commit to is left unattributed rather than guessed — an unlabelled
paragraph costs a connection, a wrongly-labelled one invents a claim about a
real person.

Requires `pip install anthropic` and an API key.
"""

from __future__ import annotations

import json
import re

DEFAULT_MODEL = "claude-opus-5"
# Assignment is judgement over a long input, not deep reasoning; medium keeps
# quality without paying to deliberate over every paragraph of thirty hours of
# talk. Raise it if a corpus has more than a couple of voices per episode.
EFFORT = "medium"
PARAGRAPHS_PER_REQUEST = 40

SYSTEM = """\
You attribute transcript paragraphs to named speakers.

You get a video's metadata and a numbered run of transcript paragraphs from it.
Decide who is speaking in each paragraph and return that as JSON.

Identifying the people:
- The channel is the host or interviewer.
- The description usually names the guest, spelled correctly. Prefer its
  spelling over the transcript's: speech recognition mangles names, so the
  transcript may say "Danny shean" where the description says "Daniel Sheehan".
- Use full names as given in the metadata. If a third voice appears (a clip,
  a caller, a co-host) name them if the transcript makes it clear, otherwise
  describe them ("unnamed caller").
- Someone merely *discussed* is not a speaker. Only people whose own words
  appear get labels.

Attributing paragraphs:
- Interviews alternate: questions, framing and sponsor reads are the host;
  extended first-person accounts and subject-matter detail are the guest.
- A paragraph may contain both voices where a cue spans a handover. Attribute
  it to whoever speaks most of it.
- Give each paragraph a confidence: "high" when the turn-taking is
  unambiguous, "low" when you are inferring from topic alone.
- Where you genuinely cannot tell, use null for the speaker rather than
  guessing. A missing label is recoverable; a wrong one is not.

Return only JSON of this shape, with one entry per input paragraph:

{"speakers": ["Full Name", "Other Name"],
 "labels": [{"n": 1, "speaker": "Full Name", "confidence": "high"},
            {"n": 2, "speaker": null, "confidence": "low"}]}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "speakers": {"type": "array", "items": {"type": "string"}},
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "speaker": {"type": ["string", "null"]},
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                },
                "required": ["n", "speaker", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["speakers", "labels"],
    "additionalProperties": False,
}


class NotAvailable(LookupError):
    """The anthropic SDK or an API key is missing — not a failure of the fetch."""


def _client():
    try:
        import anthropic
    except ImportError as e:
        raise NotAvailable("--speakers needs the anthropic package: "
                           "pip install anthropic") from e
    try:
        return anthropic.Anthropic()
    except Exception as e:  # no key, unusable profile
        raise NotAvailable(f"could not create an Anthropic client: {e}") from e


def _context(t: dict) -> str:
    """What the video says about itself. The description earns its place here."""
    lines = [f"Title: {t.get('title', '')}",
             f"Channel (the host): {t.get('channel') or 'unknown'}"]
    if t.get("upload_date"):
        lines.append(f"Published: {t['upload_date']}")
    if desc := (t.get("description") or "").strip():
        # Enough to carry the guest's introduction; not the whole sponsor tail.
        lines.append(f"Description:\n{desc[:1500]}")
    return "\n".join(lines)


def _ask(client, context: str, paragraphs: list[tuple[int, str]], model: str) -> dict:
    numbered = "\n\n".join(f"[{n}] {text}" for n, text in paragraphs)
    message = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": EFFORT,
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user",
                   "content": f"{context}\n\n--- transcript ---\n\n{numbered}"}],
    )
    # A refusal is a 200 with no content, not an exception; indexing content[0]
    # would crash on it.
    if message.stop_reason == "refusal":
        return {}
    text = "".join(b.text for b in message.content if b.type == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def attribute(t: dict, model: str = DEFAULT_MODEL,
              per_request: int = PARAGRAPHS_PER_REQUEST,
              progress=None) -> dict:
    """Attribute one transcript's paragraphs to named speakers.

    Returns {"speakers": [names], "labels": [{speaker, confidence} | None ...]},
    one label per paragraph, None where the model wouldn't commit or a request
    failed. Paragraphs are sent in overlapping context: each request sees the
    preceding paragraph so a handover at a batch boundary is still visible.
    """
    client = _client()
    paras = t.get("paragraphs") or []
    context = _context(t)
    labels: list[dict | None] = [None] * len(paras)
    names: list[str] = []

    for start in range(0, len(paras), per_request):
        window = paras[start:start + per_request]
        numbered = [(start + i + 1, p["text"]) for i, p in enumerate(window)]
        try:
            got = _ask(client, context, numbered, model)
        except Exception as e:
            if _fatal(e):
                raise
            got = {}

        for name in got.get("speakers") or []:
            if name and name not in names:
                names.append(name)
        for entry in got.get("labels") or []:
            n = entry.get("n")
            # Trust the numbering only where it lands on a paragraph we sent.
            if isinstance(n, int) and start < n <= start + len(window):
                labels[n - 1] = {"speaker": entry.get("speaker"),
                                 "confidence": entry.get("confidence") or "low"}
        if progress:
            progress(min(start + per_request, len(paras)), len(paras))

    return {"speakers": names, "labels": labels,
            "model": model,
            "attributed": sum(1 for x in labels if x and x.get("speaker")),
            "unattributed": sum(1 for x in labels if not (x and x.get("speaker")))}


def _fatal(e: Exception) -> bool:
    """Bad key or missing model fails identically for every batch — stop now."""
    return type(e).__name__ in {
        "AuthenticationError", "PermissionDeniedError", "NotFoundError",
    }


def estimate_usd(word_count: int, model: str = DEFAULT_MODEL) -> float | None:
    """Rough cost, before spending someone else's money. None if model unknown.

    The shape of this task is what makes it affordable: the transcript is input,
    and the response is a short label per paragraph. Output is assumed at 2% of
    input, which is generous for `{"n": 12, "speaker": "...", ...}` against a
    110-word paragraph.
    """
    rates = {                                  # USD per million (input, output)
        "claude-opus-5": (5.0, 25.0),
        "claude-sonnet-5": (3.0, 15.0),
        "claude-haiku-4-5": (1.0, 5.0),
    }.get(model)
    if not rates:
        return None
    mtok_in = word_count * 1.35 / 1_000_000    # ~1.35 tokens per English word
    return mtok_in * rates[0] + mtok_in * 0.02 * rates[1]


def apply(t: dict, result: dict) -> dict:
    """Fold labels into a transcript dict without disturbing the verbatim text.

    Speakers land beside each paragraph, never inside `text` — ADR 0011's rule
    that generated content stays in its own field, so a reader can always tell
    which parts of the document the captions actually said.
    """
    for p, label in zip(t.get("paragraphs") or [], result.get("labels") or []):
        if label and label.get("speaker"):
            p["speaker"] = label["speaker"]
            p["speaker_confidence"] = label.get("confidence", "low")
    t["speakers"] = result.get("speakers") or []
    t["speakers_by"] = result.get("model", "")
    return t
