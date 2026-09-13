"""Configuration shared by the indexing CLI and MCP server."""

import yaml

from fixdoc.core.embedding import DEFAULT_MODEL


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
