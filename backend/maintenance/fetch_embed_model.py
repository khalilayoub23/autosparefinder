#!/usr/bin/env python3
"""
Script:  maintenance/fetch_embed_model.py
Purpose: Download the local multilingual embedding model into the persistent
         worker_state volume.

Why the model is NOT baked into the Docker image:
  • 458 MB of weights would be re-pulled on every image build and pushed into
    every layer cache.
  • `/app/state` is the `worker_state` volume, so the weights survive container
    recreate AND image rebuild, and only download once.

Only the ONNX weights are fetched — NOT the PyTorch ones. onnxruntime is ~50MB
against torch's ~800MB, and this box has 12GB RAM with NO SWAP, where an OOM is
a hard kill rather than a slowdown.

Idempotent: skips the download when the files are already present.

Usage:  python3 /app/maintenance/fetch_embed_model.py [--force]
Data Modified: /app/state/models/minilm-multilingual (filesystem only)
Last Updated:  2026-07-28
"""
from __future__ import annotations

import argparse
import os
import sys

REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEST = os.getenv("EMBED_MODEL_DIR", "/app/state/models/minilm-multilingual")

# Everything needed for ONNX inference + tokenization, and nothing else.
PATTERNS = [
    "onnx/model.onnx",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "config.json",
    "sentence_bert_config.json",
    "1_Pooling/config.json",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the files are already present")
    args = ap.parse_args()

    onnx = os.path.join(DEST, "onnx", "model.onnx")
    tok = os.path.join(DEST, "tokenizer.json")
    if not args.force and os.path.exists(onnx) and os.path.exists(tok):
        size = os.path.getsize(onnx) / 1e6
        print(f"[fetch_embed_model] already present ({size:.0f} MB) — skipping")
        return 0

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("[fetch_embed_model] huggingface_hub not installed", file=sys.stderr)
        return 1

    print(f"[fetch_embed_model] downloading {REPO} -> {DEST}", flush=True)
    snapshot_download(REPO, local_dir=DEST, allow_patterns=PATTERNS)
    print(f"[fetch_embed_model] done: {os.path.getsize(onnx) / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
