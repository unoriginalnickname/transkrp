"""Optional: work out who said what, by name — through the `claude` CLI.

The caption tracks don't say. Measured on a 30-episode interview playlist, not
one carried a single `>>` speaker marker — so a corpus built for cross-
referencing people has no idea which of two voices is talking, which is fatal
for the one thing it's for.

**This shells out to the `claude` command rather than calling the API.** That is
the whole design constraint: no API key, no separate billing, no `anthropic`
dependency. If you can run `claude` in a terminal, you can run this. The cost is
whatever your existing Claude Code plan already charges you, and the tool has no
opinion about it.

It reads names off the video's own metadata rather than guessing them. The
channel is the host; the description's first sentence names the guest, spelled
correctly by a human — which matters because speech recognition mangles exactly
those words ("Danny shean" for Daniel Sheehan, "Elon hubber" for L. Ron
Hubbard). Feeding it the description is what turns "SPEAKER_01" into a name you
can cross-reference.

Attribution is generated, so it follows ADR 0011: opt-in, kept in its own field,
never inside the verbatim text, announced in the output. Every label carries the
model's own confidence, and a paragraph it won't commit to is left unattributed
rather than guessed — a missing label costs a connection, a wrong one invents a
claim about a real person.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import ontology

PARAGRAPHS_PER_REQUEST = 40
# Per call. A batch of 40 paragraphs is a few thousand words in and a few
# hundred tokens out; anything slower than this is a hang, not a long think.
TIMEOUT = 600

PROMPT = """\
You attribute transcript paragraphs to named speakers.

Below is a video's metadata and a numbered run of transcript paragraphs from it.
Decide who is speaking in each paragraph.

Identifying the people:
- The channel is the host or interviewer.
- The description usually names the guest, spelled correctly. Prefer its
  spelling over the transcript's: speech recognition mangles names, so the
  transcript may say "Danny shean" where the description says "Daniel Sheehan".
- Use full names as given in the metadata. If a third voice appears (a clip, a
  caller, a co-host) name them if the transcript makes it clear, otherwise
  describe them ("unnamed caller").
- Someone merely *discussed* is not a speaker. Only people whose own words
  appear get a label.

Attributing paragraphs:
- Interviews alternate: questions, framing and sponsor reads are the host;
  extended first-person accounts and subject-matter detail are the guest.
- A paragraph may span a handover. Attribute it to whoever speaks most of it.
- confidence is "high" when the turn-taking is unambiguous, "low" when you are
  inferring from topic alone.
- Where you genuinely cannot tell, use null for the speaker rather than
  guessing. A missing label is recoverable; a wrong one is not.

Reply with JSON and nothing else — no prose, no code fence — with exactly one
label per input paragraph, keeping the [n] numbers you were given:

{"speakers": ["Full Name", "Other Name"],
 "labels": [{"n": 1, "speaker": "Full Name", "confidence": "high"},
            {"n": 2, "speaker": null, "confidence": "low"}]}
"""


class NotAvailable(LookupError):
    """The `claude` CLI isn't installed or isn't logged in."""


def available() -> bool:
    return shutil.which("claude") is not None


def _run(prompt: str, model: str | None) -> str:
    if not available():
        raise NotAvailable(
            "--speakers needs the `claude` command on PATH "
            "(https://claude.com/claude-code). No API key required.")
    cmd = ["claude", "-p", "--output-format", "text"]
    if model:
        cmd += ["--model", model]
    try:
        done = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=TIMEOUT, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        raise LookupError(f"claude did not answer within {TIMEOUT}s") from e
    except OSError as e:
        raise NotAvailable(f"could not run claude: {e}") from e
    if done.returncode != 0:
        err = (done.stderr or "").strip().splitlines()
        raise LookupError(f"claude exited {done.returncode}: "
                          f"{err[-1] if err else 'no output'}")
    return done.stdout


def _parse(reply: str) -> dict:
    """Pull the JSON object out of a CLI reply.

    `-p` returns the assistant's text, which is usually the bare object but can
    arrive wrapped in a code fence or a sentence. Rather than insisting on
    perfect obedience, find the outermost braces — the alternative is throwing
    away a good answer over its packaging.
    """
    fenced = re.search(r"```(?:json)?\s*(.+?)```", reply, re.S)
    if fenced:
        reply = fenced.group(1)
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        got = json.loads(reply[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return got if isinstance(got, dict) else {}


def _context(t: dict) -> str:
    """What the video says about itself. The description earns its place here."""
    lines = [f"Title: {t.get('title', '')}",
             f"Channel (the host): {t.get('channel') or 'unknown'}"]
    if t.get("upload_date"):
        lines.append(f"Published: {t['upload_date']}")
    if desc := (t.get("description") or "").strip():
        # Enough to carry the guest's introduction, not the whole sponsor tail.
        lines.append(f"Description:\n{desc[:1500]}")
    return "\n".join(lines)


def attribute(t: dict, model: str | None = None,
              per_request: int = PARAGRAPHS_PER_REQUEST,
              progress=None, retry: bool = True) -> dict:
    """Attribute one transcript's paragraphs to named speakers.

    Returns {"speakers": [names], "labels": [{speaker, confidence} | None, ...]},
    one entry per paragraph, None where the model wouldn't commit or a batch
    failed. Paragraphs keep absolute numbering across batches so a label can
    never land on a neighbour.
    """
    paras = t.get("paragraphs") or []
    context = _context(t)
    labels: list[dict | None] = [None] * len(paras)
    names: list[str] = []

    for start in range(0, len(paras), per_request):
        window = paras[start:start + per_request]
        numbered = "\n\n".join(
            f"[{start + i + 1}] {p['text']}" for i, p in enumerate(window))
        try:
            got = _parse(_run(
                f"{PROMPT}\n\n{context}\n\n--- transcript ---\n\n{numbered}", model))
        except NotAvailable:
            raise                      # identical for every batch; stop now
        except LookupError:
            got = {}                   # this batch keeps its verbatim paragraphs

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

    result = {"speakers": names, "labels": labels,
              "model": model or "claude (cli default)",
              "attributed": sum(1 for x in labels if x and x.get("speaker")),
              "unattributed": sum(1 for x in labels if not (x and x.get("speaker")))}
    return validated(t, result, model, retry=retry)


def validated(t: dict, result: dict, model: str | None, retry: bool = True) -> dict:
    """Run the answer past the domain model before anyone sees it.

    This is the ledger check, and the loop Coyle describes: an unreasonable
    result goes back to the model with the reason, once. One retry rather than a
    loop on purpose — his own warning is that loops drift and cost money, and a
    second disagreement is a signal to stop and report, not to keep asking.
    """
    corrected, violations = ontology.check(t, result)
    if not violations or not retry:
        corrected["violations"] = [v.detail for v in violations]
        return corrected

    known = ontology.known_people(t)
    hint = ontology.retry_hint(violations, known)
    paras = t.get("paragraphs") or []
    numbered = "\n\n".join(f"[{i}] {p['text']}" for i, p in enumerate(paras, 1))
    try:
        second = _parse(_run(
            f"{PROMPT}\n\n{_context(t)}\n\n{hint}\n\n"
            f"--- transcript ---\n\n{numbered}", model))
    except LookupError:
        second = {}

    if not second.get("labels"):
        corrected["violations"] = [v.detail for v in violations]
        return corrected

    retried = {"speakers": second.get("speakers") or [],
               "labels": [None] * len(paras),
               "model": result.get("model", "")}
    for entry in second["labels"]:
        n = entry.get("n")
        if isinstance(n, int) and 0 < n <= len(paras):
            retried["labels"][n - 1] = {"speaker": entry.get("speaker"),
                                        "confidence": entry.get("confidence") or "low"}

    second_pass, still = ontology.check(t, retried)
    # Keep whichever answer the domain model likes better. A retry that breaks
    # more constraints than the original is not an improvement.
    if len(still) < len(violations):
        second_pass["violations"] = [v.detail for v in still]
        second_pass["retried"] = True
        return second_pass
    corrected["violations"] = [v.detail for v in violations]
    return corrected


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
    # Carried into the document rather than logged and forgotten: a reader
    # deciding whether to trust an attribution needs to know the domain model
    # objected to it.
    if flagged := result.get("violations"):
        t["speakers_flagged"] = flagged
    return t
