"""
multilingual_bert.py — thin wrapper kept for backwards compatibility.

The project uses sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
via hf_client.hf_embed().  Use that function for all new code.

This module is intentionally NOT imported by the main application.
"""
from hf_client import _get_embed_model


class MultilingualBERTHandler:
    """Thin facade over the project's sentence-transformers model."""

    def encode_text(self, text: str):
        model = _get_embed_model()
        return model.encode(text)


# Do NOT instantiate at module level — _get_embed_model() loads a ~90 MB model
# lazily on first use.  Instantiate only when needed:
#   handler = MultilingualBERTHandler()
