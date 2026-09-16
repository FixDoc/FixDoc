"""Local embedding backend: text in, vector out, nothing leaves the machine.

fastembed (ONNX, CPU) is a lazy optional import so the base install stays
lean and tests never touch the ~130MB model download. Everything in the
engine takes a plain ``embed_fn`` callable, so swapping backends (or
injecting a fake in tests) never touches engine code.
"""
import yaml

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"  # small, CPU-fast, license-clean, offline

def resolve_model(root, override=None):
    if override is not None:
        model = override
    else:
        path = root / ".fixdoc" / "config.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise ValueError(".fixdoc/config.yaml must contain a YAML mapping")
        model = config.get("embedding_model") or DEFAULT_MODEL
    if not isinstance(model, str) or not model.strip():
        raise ValueError("embedding_model must be a nonempty string")
    return model

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
