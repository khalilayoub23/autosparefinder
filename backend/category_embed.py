"""
category_embed.py — local multilingual sentence embeddings for part categorization.

MODEL: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (117.7M params,
Apache-2.0), run through ONNX Runtime — NOT PyTorch. torch would add ~800MB to the
image and ~1.5GB RSS; onnxruntime is ~50MB and faster on CPU. The box has 12GB and
NO SWAP, so an OOM is a hard kill — the lighter runtime is not a preference, it is
the reason this is safe to run at all.

WHY EMBEDDINGS HELP HERE (the keyword matcher's structural blind spots):
  • WORD ORDER — OEM catalogs name parts noun-first: 5,888 rows literally start
    with "Clamp ..." ("Clamp Hose", not "Hose Clamp"). Keyword rules need every
    reversed compound listed by hand; embeddings are order-robust by construction.
  • MULTILINGUAL — one model covers Hebrew, Arabic and English, so a Hebrew part
    name and its English equivalent land near each other without a translation
    table. (The model card lists `he` and `ar` explicitly.)
  • SYNONYMS / TRUNCATIONS — "Cover-cushio", "absorber assembly shock" and
    supplier-specific phrasing do not need their own rule.

WHAT IT IS NOT: a replacement for the keyword rules. It is the same "helper for a
stuck worker" contract the owner set for the LLM assist — deterministic rules
decide first, this only speaks when they have no opinion. Rules are exact,
auditable and free; embeddings are approximate and must never override them.

Design:
  • NEAREST EXEMPLAR, not a centroid. Each category's keyword phrases are
    embedded INDIVIDUALLY and a query takes the single closest phrase's category.
    A centroid was tried first and measured badly (2026-07-28): averaging ~160
    diverse phrases blurs the class, and it put "Brake Pad Set Front" in
    belts-chains and Arabic "فلتر زيت المحرك" (engine oil FILTER) in fluids.
    Max-similarity over exemplars keeps each sense sharp, which matters because
    these classes are genuinely multi-modal (brakes = pads AND hoses AND
    calipers AND sensors).
  • classify() returns (category, score) and refuses below MIN_SCORE, because a
    confidently-wrong category is worse than the catch-all.

Data Modified: none (pure inference)
Last Updated:  2026-07-28
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("category_embed")

MODEL_DIR = os.getenv("EMBED_MODEL_DIR", "/app/state/models/minilm-multilingual")
ONNX_PATH = os.path.join(MODEL_DIR, "onnx", "model.onnx")
TOKENIZER_PATH = os.path.join(MODEL_DIR, "tokenizer.json")

# Cosine floor. Below this the model is guessing and we return None so the caller
# keeps 'כללי'. Tuned against the measured benchmark, not picked by feel.
MIN_SCORE = float(os.getenv("EMBED_MIN_SCORE", "0.45"))
MAX_TOKENS = 64          # part names are short; caps memory and time per batch

_lock = threading.Lock()
_session = None
_tokenizer = None
_centroids: Optional["object"] = None      # np.ndarray (n_cats, dim)
_centroid_labels: List[str] = []


def available() -> bool:
    """True if the model files are present and importable."""
    if not (os.path.exists(ONNX_PATH) and os.path.exists(TOKENIZER_PATH)):
        return False
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        from tokenizers import Tokenizer  # noqa: F401
        return True
    except ImportError:
        return False


def _load() -> bool:
    global _session, _tokenizer
    if _session is not None:
        return True
    with _lock:
        if _session is not None:
            return True
        if not available():
            return False
        import onnxruntime
        from tokenizers import Tokenizer

        opts = onnxruntime.SessionOptions()
        # One thread per call keeps this from fighting the harvester / postgres
        # for the 6 vCPUs. Throughput comes from batching, not from thread count.
        opts.intra_op_num_threads = int(os.getenv("EMBED_THREADS", "2"))
        opts.inter_op_num_threads = 1
        _session = onnxruntime.InferenceSession(
            ONNX_PATH, sess_options=opts, providers=["CPUExecutionProvider"])
        _tokenizer = Tokenizer.from_file(TOKENIZER_PATH)
        _tokenizer.enable_truncation(max_length=MAX_TOKENS)
        _tokenizer.enable_padding(length=None)
        logger.info("embedding model loaded from %s", MODEL_DIR)
        return True


def embed(texts: Sequence[str]):
    """Mean-pooled, L2-normalised embeddings for a batch of texts."""
    import numpy as np
    if not _load():
        raise RuntimeError("embedding model unavailable")
    enc = _tokenizer.encode_batch([t[:400] or " " for t in texts])
    ids = np.array([e.ids for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask for e in enc], dtype=np.int64)

    feed = {"input_ids": ids, "attention_mask": mask}
    names = {i.name for i in _session.get_inputs()}
    if "token_type_ids" in names:
        feed["token_type_ids"] = np.zeros_like(ids)

    out = _session.run(None, feed)[0]                      # (b, seq, dim)
    m = mask[..., None].astype(out.dtype)
    summed = (out * m).sum(axis=1)
    counts = np.clip(m.sum(axis=1), 1e-9, None)
    vecs = summed / counts                                  # mean pooling
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-9, None)


def build_centroids(force: bool = False) -> int:
    """
    Embed every category's exemplar phrases INDIVIDUALLY (not averaged).

    The slug itself ('body-exterior') is a weak query, so exemplars come from the
    category's own curated keyword rules — already multilingual part vocabulary —
    plus its display names.
    """
    global _centroids, _centroid_labels
    import numpy as np
    import category_map as cm

    if _centroids is not None and not force:
        return len(_centroid_labels)
    if not _load():
        return 0

    phrases: Dict[str, List[str]] = {}
    for cat, rtl_kws, en_kws in cm.RULES:
        if cat == cm.CATCH_ALL:
            continue
        bucket = phrases.setdefault(cat, [])
        bucket.extend(k.strip() for k in en_kws if len(k.strip()) >= 4)
        bucket.extend(k.strip() for k in rtl_kws if len(k.strip()) >= 3)
    for cat, disp in cm.DISPLAY.items():
        if cat == cm.CATCH_ALL or cat not in phrases:
            continue
        phrases[cat].extend([disp.get("en", ""), disp.get("he", ""), disp.get("ar", "")])

    labels: List[str] = []
    texts: List[str] = []
    for cat, ph in phrases.items():
        for p in dict.fromkeys(ph):
            if p:
                labels.append(cat)
                texts.append(p)

    mats = []
    for i in range(0, len(texts), 256):
        mats.append(embed(texts[i:i + 256]))
    _centroid_labels = labels
    _centroids = np.vstack(mats) if mats else None
    logger.info("embedded %d exemplar phrases across %d categories",
                len(labels), len(set(labels)))
    return len(labels)


def classify(texts: Sequence[str]) -> List[Tuple[Optional[str], float]]:
    """
    [(category, score), ...] — category is None when the best score is below
    MIN_SCORE. A confidently-wrong category is worse than the catch-all.
    """
    import numpy as np
    if not build_centroids():
        return [(None, 0.0)] * len(texts)
    sims = embed(texts) @ _centroids.T          # cosine to EVERY exemplar phrase
    best = sims.argmax(axis=1)                  # nearest single exemplar wins
    out: List[Tuple[Optional[str], float]] = []
    for row, j in zip(sims, best):
        score = float(row[j])
        out.append((_centroid_labels[j], score) if score >= MIN_SCORE else (None, score))
    return out


def classify_one(text: str) -> Tuple[Optional[str], float]:
    return classify([text])[0]
