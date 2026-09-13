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
import urllib.request

DEFAULT_IMPORT_MODEL = "claude-haiku-4-5"  # user decision: cheap tier fits import volume
DEFAULT_THRESHOLD = 0.6

DEFAULT_RUBRIC = (
    "confidence is the probability a knowledgeable reviewer will accept this "
    "entry as accurate. Anchors: 0.9+ the thread explicitly confirms the fix "
    "worked; 0.7 the resolution is stated and the outcome implied; 0.5 a fix "
    "is mentioned but never confirmed; below 0.5 you are guessing."
)

# TODO(eval): this prompt's effectiveness must be measured, not assumed. The
# eval harness gets an extraction-quality set (threads with known-good expected
# entries, like the judge eval set) so prompt changes are justified by numbers
# and regressions are caught. Expect this text to change over time.
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


def _openai_chat(base_url, api_key, model, system, user):
    """Minimal OpenAI-compatible chat call over urllib — the de-facto protocol
    of Ollama, vLLM, LM Studio, OpenRouter, and most gateways. This is what
    makes extraction provider-agnostic (and fully LOCAL when base_url points
    at an Ollama on localhost: thread text never leaves the machine)."""
    payload = {
        "model": model,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        data = json.load(response)
    return data["choices"][0]["message"]["content"]


def extract_thread(
    thread_text,
    rubric=None,
    model=DEFAULT_IMPORT_MODEL,
    api_key=None,
    base_url=None,
    provider="anthropic",
):
    """One thread -> parsed extraction dict. Raises on API/parse failure; the
    caller decides what a failed thread costs (skip and report, never crash
    the whole run).

    provider: "anthropic" (default) or "openai-compatible" — the latter works
    with any endpoint speaking the OpenAI chat protocol, including local
    models (Ollama/vLLM), so no Slack content has to leave the machine.
    """
    system = _SYSTEM_TEMPLATE.format(rubric=rubric or DEFAULT_RUBRIC)
    if provider == "openai-compatible":
        if not base_url:
            raise RuntimeError(
                "provider openai-compatible requires import.base_url "
                "(e.g. http://localhost:11434/v1 for a local Ollama)"
            )
        return _parse_extraction(_openai_chat(base_url, api_key, model, system, thread_text))
    if provider != "anthropic":
        raise RuntimeError(f"unknown import.provider: {provider!r}")
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
        system=system,
        messages=[{"role": "user", "content": thread_text}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_extraction(text)
