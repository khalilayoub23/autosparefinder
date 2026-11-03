#!/bin/bash
# Disposable-volume persistence + permission check. Production mount config is read (docker inspect) but never touched.
# usage: persistence_check.sh <merged-candidate-tree> <env-file with sandbox env>
set -u
TREE=$1; ENVF=$2; AUD=$(cd "$(dirname "$0")" && pwd); VOL=asf_state_audit_$$
IMG=autosparefinder-backend:latest
echo "== production mount config (read-only inspect)"; docker inspect autospare_backend --format '{{range .Mounts}}{{.Type}} {{.Name}} -> {{.Destination}} mode={{.Mode}} rw={{.RW}}{{"\n"}}{{end}}' | grep -E "/app( |/state|/uploads)"; docker exec autospare_backend id
docker volume create $VOL >/dev/null
RUN="docker run --rm --network sandbox_net --env-file $ENVF -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -v $TREE:/app:ro -v $VOL:/app/state -v $AUD:/audit:ro -w / $IMG"
echo "== A: writer (uid 0, app tree READ-ONLY, state = disposable named volume, cwd=/)"
docker rm -f asf_audit_A >/dev/null 2>&1
docker run --name asf_audit_A --network sandbox_net --env-file $ENVF -e RUN_DB_MUTATION_TESTS=1 -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -v $TREE:/app:ro -v $VOL:/app/state -v $AUD:/audit:ro -w / $IMG python3 /audit/persist_writer.py
echo "== A: container writable-layer changes (docker diff) - anything outside /app/state is NOT on the volume"
docker diff asf_audit_A | grep -v "^C /tmp\|^A /tmp" | head -40
docker rm asf_audit_A >/dev/null
echo "== B: NEW container, same volume (reads back through real modules)"
$RUN python3 /audit/persist_reader.py
echo "== volume tree"; docker run --rm -v $VOL:/s $IMG sh -c 'cd /s && find . -maxdepth 2 | sort | head -60; stat -c "%u:%g %a %n" . logs inproc_jobs 2>/dev/null'
echo "== C: NON-root (uid 1000) against the root-owned volume: read ok? write refused with a clear message?"
docker run --rm --user 1000:1000 --network sandbox_net --env-file $ENVF -e HOME=/tmp -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -v $TREE:/app:ro -v $VOL:/app/state -v $AUD:/audit:ro -w / $IMG python3 /audit/persist_nonroot.py
docker volume rm $VOL >/dev/null && echo "disposable volume removed"
