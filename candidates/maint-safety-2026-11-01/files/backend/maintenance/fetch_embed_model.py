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

Idempotent: skips the download only when the cached files are COMPLETE AND LOADABLE (every required file present and
non-empty, the ONNX graph opens in onnxruntime and runs, the tokenizer opens). Presence alone is not proof: an interrupted copy
or a truncated file used to be reported "already present - skipping" and left the embedder broken. After a download the files
are checked against what the source publishes (byte size of every file, SHA-256 of the LFS-stored ones); a mismatch, a missing
file or an unloadable model exits non-zero - it never reports success for an artifact that is not there.

Usage:  python3 /app/maintenance/fetch_embed_model.py [--force]   (exit 0 = a valid model is in place, 1 = it is not)
Data Modified: /app/state/models/minilm-multilingual (filesystem only)
Last Updated:  2026-11-02
"""
from __future__ import annotations

import argparse
import os
import sys

REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
import app_paths  # state paths: app-root based, never the cwd; a relative override resolves against the app root
DEST = str(app_paths.state_path("EMBED_MODEL_DIR", "models", "minilm-multilingual"))   # same dir category_embed reads

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


def _problems(dest: str) -> list:
    """Why the model in `dest` is NOT usable (empty list = usable). Presence + non-empty for every required file, then the real
    loaders: the ONNX graph must open and produce a (1, seq, dim) output for a tokenised probe, the tokenizer must open."""
    out = []
    for rel in PATTERNS:
        f = os.path.join(dest, rel)
        if not os.path.isfile(f):
            out.append(f"missing: {rel}")
        elif os.path.getsize(f) == 0:
            out.append(f"empty: {rel}")
    if out:
        return out
    try:
        import numpy as np
        import onnxruntime
        from tokenizers import Tokenizer
    except ImportError as exc:
        return [f"cannot verify, dependency missing: {exc}"]
    try:
        tok = Tokenizer.from_file(os.path.join(dest, "tokenizer.json"))
    except Exception as exc:
        return [f"tokenizer.json unreadable: {exc}"]
    try:
        opts = onnxruntime.SessionOptions()
        opts.intra_op_num_threads = 1
        sess = onnxruntime.InferenceSession(os.path.join(dest, "onnx", "model.onnx"), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        enc = tok.encode("brake pad")
        ids = np.array([enc.ids], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": np.array([enc.attention_mask], dtype=np.int64)}
        if "token_type_ids" in {i.name for i in sess.get_inputs()}:
            feed["token_type_ids"] = np.zeros_like(ids)
        res = sess.run(None, feed)[0]
        if res.ndim != 3 or res.shape[0] != 1 or res.shape[1] != ids.shape[1] or not np.isfinite(res).all():
            return [f"model.onnx produced an unexpected output {getattr(res, 'shape', None)}"]
    except Exception as exc:
        return [f"model.onnx not loadable: {str(exc)[:200]}"]
    return []


def _published_mismatches(dest: str) -> tuple:
    """(mismatches as [(relative path, reason)], note). Compares every fetched file with the source's own metadata: size for all, SHA-256 for LFS files (the
    authoritative checksum the Hub publishes). If the metadata cannot be fetched the note says so and only the load check applies."""
    import hashlib
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(REPO, files_metadata=True)
    except Exception as exc:
        return [], f"published checksums unavailable ({type(exc).__name__}) - load check only"
    meta = {s.rfilename: s for s in (info.siblings or [])}
    bad = []
    for rel in PATTERNS:
        m, f = meta.get(rel), os.path.join(dest, rel)
        if m is None:
            bad.append((rel, "not listed by the source"))
            continue
        if m.size is not None and os.path.getsize(f) != m.size:
            bad.append((rel, f"{os.path.getsize(f)} bytes, source publishes {m.size}"))
            continue
        want = getattr(getattr(m, "lfs", None), "sha256", None)
        if want:
            h = hashlib.sha256()
            with open(f, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != want:
                bad.append((rel, f"sha256 {h.hexdigest()[:12]}... != published {want[:12]}..."))
    return bad, f"checked against source revision {info.sha[:12]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the files are already present")
    args = ap.parse_args()

    onnx = os.path.join(DEST, "onnx", "model.onnx")
    present = os.path.exists(onnx) or os.path.exists(os.path.join(DEST, "tokenizer.json"))
    if present and not args.force:
        why = _problems(DEST)
        if not why:
            print(f"[fetch_embed_model] already present and loadable ({os.path.getsize(onnx) / 1e6:.0f} MB) — skipping")
            return 0
        print(f"[fetch_embed_model] cached model is NOT valid ({'; '.join(why)}) — downloading again", flush=True)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("[fetch_embed_model] huggingface_hub not installed", file=sys.stderr)
        return 1

    print(f"[fetch_embed_model] downloading {REPO} -> {DEST}", flush=True)
    # force_download when something is already there that failed verification (or --force): a half-written file must not be reused.
    try:
        snapshot_download(REPO, local_dir=DEST, allow_patterns=PATTERNS, force_download=present)
    except Exception as exc:                      # network, auth, disk full ...: say what and where, never a bare traceback
        print(f"[fetch_embed_model] FAILED - download of {REPO} into {DEST} did not complete: "
              f"{type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 1
    why = _problems(DEST)
    mism, note = _published_mismatches(DEST)
    for rel, reason in mism:
        why.append(f"{rel}: {reason}")
        try:                                       # a file that disagrees with the source must not be trusted by the next run
            os.remove(os.path.join(DEST, rel))
        except OSError:
            pass
    if why:
        print(f"[fetch_embed_model] FAILED - the downloaded model is not valid: {'; '.join(why)}", file=sys.stderr)
        return 1
    print(f"[fetch_embed_model] done: {os.path.getsize(onnx)} bytes, loadable; {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
