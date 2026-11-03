"""Generates evidence/path_audit/{before,after}.json and files/docs/IMPORTER_PATH_AUDIT.inventory.md from the ONE scanner
(files/backend/tests/path_audit_scan.py). Run: python3 evidence/path_audit.py <baseline-backend> <merged-candidate-backend>"""
import collections, json, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "files", "backend", "tests"))
from pathlib import Path
from path_audit_scan import scan_tree

BEFORE, AFTER = Path(sys.argv[1]), Path(sys.argv[2])
CAND = Path(HERE).parent / "files" / "backend"
OUT = Path(HERE) / "path_audit"


def refs(root, rel):
    stem = Path(rel).stem
    out = subprocess.run(["grep", "-rlE", rf"\b{re.escape(stem)}\b", "--include=*.py", "--include=*.sh", "--include=*.yml", str(root)],
                         capture_output=True, text=True).stdout.split()
    return len([f for f in out if not f.endswith(rel)])


def exists_in_container(root, p):
    if p.startswith("/app/state") or p.startswith("/app/uploads"):
        return "volume (runtime data; not in the tree)"
    if p.startswith("/app/"):
        return "yes" if (root / p[5:]).exists() else "no (absent from the app tree)"
    if p.startswith("/tmp/"):
        return "scratch (created at runtime)"
    if p.startswith("/opt/autosparefinder"):
        return "NO - /opt/autosparefinder does not exist in the container"
    return "n/a"


def inventory(root):
    hits = scan_tree(root)
    changed = {p.relative_to(CAND).as_posix() for p in CAND.rglob("*.py")}
    for h in hits:
        h["container_exists"] = exists_in_container(root, h["path"])
        h["code_refs"] = refs(root, h["file"])
        h["unused"] = h["code_refs"] == 0 and "/importers/" not in "/" + h["file"] and False
    return hits


b, a = inventory(BEFORE), inventory(AFTER)
OUT.mkdir(exist_ok=True)
for name, data in (("before", b), ("after", a)):
    (OUT / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
nfiles = {n: len([p for p in r.rglob("*") if p.suffix in (".py", ".sh") and not set(p.parts) & {"__pycache__", "alembic", "alembic_pii", "legacy", "node_modules"}
                  and p.relative_to(r).parts[0] not in ("tests", "devtests")]) for n, r in (("before", BEFORE), ("after", AFTER))}
print("files scanned:", nfiles)
for n, d in (("before", b), ("after", a)):
    print(n, len(d), dict(collections.Counter(h["class"] for h in d)), "files:", len({h['file'] for h in d}))
after_key = {(h["file"], h["path"]) for h in a}
lines = ["| file:line (baseline) | path in baseline | class (before) | exists in container | env override | after |", "|---|---|---|---|---|---|"]
for h in sorted(b, key=lambda h: (h["class"] != "BROKEN_IN_CONTAINER", h["file"], h["line"])):
    if h["class"] == "CONTAINER_COMPATIBLE":
        continue
    lines.append(f"| {h['file']}:{h['line']} | `{h['path']}` | {h['class']} | {h['container_exists']} | {'yes' if h['env_override'] else 'no'} | "
                 f"{'unchanged (HOST_ONLY by design)' if h['class']=='HOST_ONLY' else ('REMOVED' if (h['file'], h['path']) not in after_key else 'STILL PRESENT')} |")
(OUT / "broken_and_host_only.md").write_text("\n".join(lines) + "\n")
comp = [h for h in a if h["class"] == "CONTAINER_COMPATIBLE"]
rows = ["| file:line | path | exists in container | env override |", "|---|---|---|---|"]
for h in sorted(comp, key=lambda h: (h["file"], h["line"])):
    rows.append(f"| {h['file']}:{h['line']} | `{h['path']}` | {h['container_exists']} | {'yes' if h['env_override'] else 'no'} |")
(OUT / "container_compatible_after.md").write_text("\n".join(rows) + "\n")
print(len(lines) - 2, "non-compatible rows;", len(comp), "compatible rows")
