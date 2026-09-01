"""Model extraction of a fix from a Slack thread.

The emoji convention's replacement: the model reads the whole thread and
answers one structured question — did this get resolved with a stated
remediation, and if so what exactly, with what confidence? Confidence is
scored against the TEAM'S OWN rubric from .fixdoc/config.yaml when one is
set, because a threshold gate is only meaningful if the score means what
the user thinks it means.
"""

import json
import re

DEFAULT_IMPORT_MODEL = "claude-haiku-4-5"  # user decision: cheap tier fits import volume
DEFAULT_THRESHOLD = 0.6

DEFAULT_RUBRIC = (
    "confidence is the probability a knowledgeable reviewer will accept this "
    "entry as accurate. Anchors: 0.9+ the thread explicitly confirms the fix "
    "worked; 0.7 the resolution is stated and the outcome implied; 0.5 a fix "
    "is mentioned but never confirmed; below 0.5 you are guessing."
)

_SYSTEM_TEMPLATE = (
    "You extract incident knowledge from Slack threads for a team knowledge "
    "base. Read the thread and decide whether it describes a problem that was "
    "ACTUALLY RESOLVED with the remediation stated in the thread. Social "
    "threads, unanswered questions, and unresolved incidents are resolved=false.\n"
    "If resolved, extract only what the thread states — never invent: title "
    "(one line, symptom-first), symptom (as a future searcher would phrase "
    "it), root_cause (causal; null if the thread never establishes why), fix "
    "(the concrete remediation), verification (how they knew it worked; null "
    "if never stated).\n"
    "Score confidence by this rubric: {rubric}\n"
    'Respond with ONLY a JSON object: {{"resolved": bool, "confidence": '
    'float, "title": str|null, "symptom": str|null, "root_cause": str|null, '
    '"fix": str|null, "verification": str|null}}.'
)

_FIELDS = ("title", "symptom", "root_cause", "fix", "verification")


def _parse_extraction(text):
    """Pull the JSON verdict out of a model response. Raises ValueError when
    no object is found; missing fields become None; confidence clamps to 0..1."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in extraction response")
    data = json.loads(match.group(0))
    parsed = {
        "resolved": bool(data.get("resolved")),
        "confidence": min(1.0, max(0.0, float(data.get("confidence", 0.0)))),
    }
    for field in _FIELDS:
        value = data.get(field)
        parsed[field] = value if isinstance(value, str) and value.strip() else None
    return parsed


def extract_thread(
    thread_text, rubric=None, model=DEFAULT_IMPORT_MODEL, api_key=None, base_url=None
):
    """One thread -> parsed extraction dict. Raises on API/parse failure; the
    caller decides what a failed thread costs (skip and report, never crash
    the whole run)."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            'the Slack importer needs the anthropic package: pip install "fixdoc[ai]"'
        )
    client_kwargs = {}
    if api_key:
        client_kwargs["api_key"] = api_key
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=_SYSTEM_TEMPLATE.format(rubric=rubric or DEFAULT_RUBRIC),
        messages=[{"role": "user", "content": thread_text}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_extraction(text)
