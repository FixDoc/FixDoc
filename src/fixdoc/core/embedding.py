"""Local embedding backend: text in, vector out, nothing leaves the machine.

fastembed (ONNX, CPU) is a lazy optional import so the base install stays
lean and tests never touch the ~130MB model download. Everything in the
engine takes a plain ``embed_fn`` callable, so swapping backends (or
injecting a fake in tests) never touches engine code.
"""

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"  # small, CPU-fast, license-clean, offline


def get_embedder(model_name=DEFAULT_MODEL):
    """Returns embed_fn(text) -> list[float]. First call downloads the model
    (with fastembed's own progress output) into its pinned cache dir."""
    try:
        from fastembed import TextEmbedding
    except ImportError:
        raise RuntimeError(
            "the embedding model backend is not installed. " 'Run: pip install "fixdoc[embed]"'
        )
    model = TextEmbedding(model_name)

    def embed(text):
        # Plain Python floats at the boundary: fastembed yields numpy float32,
        # which json.dumps (events log) refuses downstream.
        return [float(x) for x in next(iter(model.embed([text])))]

    return embed
