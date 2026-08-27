"""Model-based worthiness classification for ingested errors.

Review decision: a regex should not have the final word on what counts as
knowledge. When an Anthropic API key is available, the model classifies every
extracted error in one batched call; the rule-based classifier remains the
keyless fallback and the safety net when the API is unreachable. Ingestion is
a human-invoked batch operation, not the query path, so this is the one place
a network call is acceptable — and it never becomes a hard dependency.
"""

import json
import re

DEFAULT_CLASSIFY_MODEL = "claude-opus-5"

_SYSTEM = (
    "You classify infrastructure error messages for a team incident knowledge "
    "base. For each numbered error decide keep=true if a documented fix would "
    "genuinely help a future engineer who hits it (authentication and "
    "permission failures, capacity and quota exhaustion, resource conflicts, "
    "state and infrastructure issues, anything environment-specific). Decide "
    "keep=false when the error message already fully explains the mistake "
    "(typos, missing required arguments, syntax and validation errors, wrong "
    "types). Respond with ONLY a JSON array of objects like "
    '{"i": <number>, "keep": <bool>} covering every item.'
)


def _parse_keep_response(text, n):
    """Extract the JSON verdict array from a model response.

    Items the model did not cover default to keep=True: an unclassified error
    goes to the queue for a human, it does not silently disappear. Raises
    ValueError when no verdict array can be found at all.
    """
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON array in classification response")
    verdicts = json.loads(match.group(0))
    keep = [True] * n
    for verdict in verdicts:
        index = verdict.get("i")
        if isinstance(index, int) and 0 <= index < n:
            keep[index] = bool(verdict.get("keep", True))
    return keep


def model_worthiness(items, model=DEFAULT_CLASSIFY_MODEL, api_key=None, base_url=None):
    """Classify items (dicts with code/address/excerpt) -> list[bool] (keep?).

    api_key/base_url come from .fixdoc/config.yaml's classification block, so
    key, endpoint, and model are configured together and always match. With
    both unset, the SDK resolves ambient credentials (ANTHROPIC_API_KEY or an
    ant auth profile). Raises on any failure; the caller falls back to rules —
    an API hiccup must never fail an ingestion run.
    """
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            'model-based classification needs the anthropic package: pip install "fixdoc[ai]"'
        )
    client_kwargs = {}
    if api_key:
        client_kwargs["api_key"] = api_key
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)
    numbered = "\n".join(
        f"{i}. code={item['code']} resource={item['address'] or '-'} :: {item['excerpt'][:200]}"
        for i, item in enumerate(items)
    )
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": numbered}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_keep_response(text, len(items))
