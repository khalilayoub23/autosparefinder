#!/bin/bash
# SP07 - REAL model download in disposable containers. usage: sp07_real_download.sh <merged-candidate-tree>
# The host disk is 100% full, so the "persistent volume" is a RAM-backed host directory (/dev/shm), shared by successive containers
# exactly like a named volume; it is removed at the end. Production files/volumes are never mounted. Internet access = default bridge.
set -u
TREE=$1; ST=/dev/shm/asf_sp07_final; M=$ST/models/minilm-multilingual; IMG=autosparefinder-backend:latest
rm -rf $ST; mkdir -p $ST
R="docker run --rm --memory 2g -v $TREE:/app:ro -v $ST:/app/state -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e HF_HUB_DISABLE_PROGRESS_BARS=1"
echo "== source metadata (published size + sha256 of onnx/model.onnx)"; $R -w / $IMG python3 -c "
from huggingface_hub import HfApi
i=HfApi().model_info('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', files_metadata=True)
print('revision', i.sha)
for s in i.siblings:
    if s.rfilename in ('onnx/model.onnx','tokenizer.json'): print(s.rfilename, s.size, s.lfs.sha256)" 2>&1 | grep -v Warning
echo "== 1. interrupted download (SIGKILL after 12 s) in container A, cwd=/"
$R -w / $IMG sh -c 'timeout -s KILL 12 python3 /app/maintenance/fetch_embed_model.py >/dev/null 2>&1; echo "rc=$?"' 
echo "state after the kill:"; find $ST -type f -printf "%s %P\n" | grep -v "\.lock$\|metadata$\|CACHEDIR\|gitignore\|trees/" | sort -k2
echo "== 2. is the interrupted state accepted? (new container, no network, cwd=/tmp)"
$R --network none -w /tmp $IMG python3 /app/maintenance/fetch_embed_model.py; echo "rc=$?"
echo "== 3. complete download, new container, cwd=/app/tests"
/usr/bin/time -f "elapsed %e s" $R -w /app/tests $IMG python3 /app/maintenance/fetch_embed_model.py 2>&1 | grep -v Warning; echo "rc=${PIPESTATUS[0]}"
echo "== 4. independent integrity check on the host (sha256sum) vs the published values above"; sha256sum $M/onnx/model.onnx $M/tokenizer.json; stat -c "%s bytes  %n" $M/onnx/model.onnx
echo "== 5. repeat run, NEW container, NO NETWORK, cwd=/: must reuse the cache and leave the file untouched"
A=$(stat -c "%i %Y" $M/onnx/model.onnx); $R --network none -w / $IMG python3 /app/maintenance/fetch_embed_model.py; echo "rc=$?"; B=$(stat -c "%i %Y" $M/onnx/model.onnx); [ "$A" = "$B" ] && echo "model.onnx untouched (inode/mtime $A)" || echo "FILE CHANGED"
echo "== 6. load through the real supported mechanism (category_embed), default paths, no network, cwd=/"
$R --network none -w / $IMG python3 -c "
import sys; sys.path.insert(0,'/app'); import category_embed as ce
print('MODEL_DIR', ce.MODEL_DIR, 'available', ce.available()); v=ce.embed(['brake pad','רפידות בלם'])
print('embed shape', v.shape, 'unit norms', [round(float((x*x).sum())**.5,4) for x in v])"
echo "== 7. truncated model.onnx (100,000,000 of 470,301,610 bytes), NO network: must not be accepted; clear failure, rc 1"
truncate -s 100000000 $M/onnx/model.onnx; $R --network none -w / $IMG python3 /app/maintenance/fetch_embed_model.py; echo "rc=$?"
echo "== 8. same truncated file, network on: repaired, re-verified against the published sha256"
$R -w / $IMG python3 /app/maintenance/fetch_embed_model.py 2>&1 | grep -v Warning; sha256sum $M/onnx/model.onnx
echo "== 9. same-size corruption of the cache (1 byte flipped) with the network on: the model still LOADS, so the offline check cannot see it"
python3 - <<PY
f="$M/onnx/model.onnx"; b=bytearray(open(f,'rb').read()); b[len(b)//2]^=1; open(f,'wb').write(b)
PY
$R -w / $IMG python3 /app/maintenance/fetch_embed_model.py 2>&1 | grep -v Warning; echo "(see docs: a fully-sized, still-parseable corruption is detected only by --force / the post-download sha256, by design)"
echo "== 10. gated real-download pytest (RUN_REAL_MODEL_DOWNLOAD=1), tmp on RAM"
docker run --rm --memory 2g --tmpfs /tmp:size=1200m -v $TREE:/app:ro -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp -e RUN_REAL_MODEL_DOWNLOAD=1 -w /app $IMG python3 -m pytest -p no:cacheprovider -q tests/test_fetch_embed_model.py 2>&1 | tail -4
rm -rf $ST; echo "RAM-backed state removed"
