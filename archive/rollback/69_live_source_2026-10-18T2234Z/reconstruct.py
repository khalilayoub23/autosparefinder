"""Reconstruct the LIVE (pre-change) source of the runtime modules from the session transcript.

The backend started 2026-10-18 22:34:13 UTC; its source on disk has since been edited in place (bind
mount, no snapshot). Every edit since then is recorded in the transcript. This script:
  1. collects every write to a target file after START (Edit / Write tool calls, python heredoc
     scripts that read-replace-write, sed -i) that did not fail;
  2. re-executes each python script with open() intercepted, logging its (old -> new) replacements
     and NOT writing anything;
  3. walks the edits newest-first, undoing each one on the current file content;
  4. PROVES each step: re-applying the edit forward to the reconstructed content must give back the
     exact later content (byte-identical), with every replaced string present exactly once.
Output: <out>/<relpath> per file + report.json. Nothing in the repository is written.
"""
import builtins
import io
import json
import re
import sys
from pathlib import Path

TRANSCRIPT, REPO, OUT = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
START = "2026-10-18T22:34:13"
TARGETS = ["BACKEND_API_ROUTES.py", "BACKEND_AI_AGENTS.py", "agents/owner_console.py", "db_update_agent.py",
           "hf_client.py", "routes/parts.py", "routes/public_api.py", "routes/brands.py",
           "manufacturer_normalization.py"]          # vehicle_model_match.py: new file -> rollback = absent


def _target_of(path):
    path = str(path).replace("\\", "/")
    for t in TARGETS:
        if path == t or path.endswith("/" + t) or path.endswith("backend/" + t):
            return t
    # bare relative names used by scripts that cd into backend/
    for t in TARGETS:
        if path == t.split("/")[-1] and "/" not in t:
            return t
    return None


uses, results = [], {}
for line in open(TRANSCRIPT):
    try:
        e = json.loads(line)
    except Exception:
        continue
    ts = e.get("timestamp") or ""
    m = e.get("message") or {}
    for b in (m.get("content") if isinstance(m.get("content"), list) else []):
        if b.get("type") == "tool_use":
            uses.append((ts, b["id"], b.get("name"), b.get("input", {})))
        elif b.get("type") == "tool_result":
            c = b.get("content")
            txt = c if isinstance(c, str) else " ".join(x.get("text", "") for x in (c or []) if isinstance(x, dict))
            results[b.get("tool_use_id")] = (bool(b.get("is_error")), txt)


class Logged(str):
    """str whose replace() is logged; count() reports 1 so the scripts' own asserts pass."""
    log = None
    path = None

    def replace(self, a, b, n=-1):
        Logged.log.append((self.path, str(a), str(b), n))
        out = Logged(str.replace(self, a, b, n))
        out.path = self.path
        return out

    def count(self, *a, **k):
        return AnyCount(1)


class AnyCount(int):
    """Compares equal to any number, so the recorded scripts' own count asserts pass during replay.
    Correctness is proven separately by the byte-identical forward replay of every step."""
    def __eq__(self, o):
        return True

    def __ne__(self, o):
        return False

    __ge__ = __le__ = __eq__

    def __hash__(self):
        return 1


def run_script(src, cwd_backend=True):
    """Execute a recorded python heredoc with a fake filesystem; return [(target, old, new, n)]."""
    Logged.log = []
    real_open = builtins.open

    def fake_open(p, mode="r", *a, **k):
        t = _target_of(p)
        if "w" in mode or "a" in mode:
            return io.StringIO()                    # writes are discarded
        s = Logged("PLACEHOLDER")       # non-target files get an inert placeholder (pairs dropped below)
        s.path = t

        class F:
            def read(_self):
                return s

            def __enter__(_self):
                return _self

            def __exit__(_self, *x):
                return False
        return F()
    g = {"__name__": "__replay__", "open": fake_open, "__builtins__": builtins}
    try:
        exec(compile(src, "<replay>", "exec"), g)
    except (FileNotFoundError, ValueError):
        pass        # a non-target file, or a slice op on the placeholder: keep the replace() pairs logged so far
    return [x for x in Logged.log if x[0]]


HEREDOC = re.compile(r"python3 - <<'?EOF'?\n(.*?)\nEOF", re.S)
events = []
for ts, tid, name, inp in uses:
    if ts < START:
        continue
    err, txt = results.get(tid, (False, ""))
    if name in ("Edit", "Write"):
        t = _target_of(inp.get("file_path", ""))
        if not t or err:
            continue
        if name == "Write":
            events.append({"ts": ts, "id": tid, "kind": "write", "target": t})
        else:
            events.append({"ts": ts, "id": tid, "kind": "edit", "target": t,
                           "pairs": [(inp["old_string"], inp["new_string"], -1 if inp.get("replace_all") else 1)]})
    elif name == "Bash":
        cmd = inp.get("command", "")
        if not any(t.split("/")[-1] in cmd for t in TARGETS):
            continue
        for mobj in HEREDOC.finditer(cmd):
            src = mobj.group(1)
            if "open(p" not in src and ".write(" not in src:
                continue
            if 'File "<stdin>"' in txt:                 # the heredoc script itself raised: nothing written
                events.append({"ts": ts, "id": tid, "kind": "py_failed", "note": txt[:200]})
                continue
            if "s.index(" in src or "s[:i]" in src:
                t = next((x for x in TARGETS if f"'{x}'" in src), None)
                events.append({"ts": ts, "id": tid, "kind": "py_slice", "target": t, "src": src, "pairs": []})
                continue
            logged = run_script(src)
            by_t = {}
            for t, a, b, n in logged:
                by_t.setdefault(t, []).append((a, b, n))
            for t, pairs in by_t.items():
                events.append({"ts": ts, "id": tid, "kind": "py", "target": t, "pairs": pairs})
        for sm in re.finditer(r"sed -i '(s/(?:[^/\\]|\\.)*/(?:[^/\\]|\\.)*/g?)' ([^;|&\n]+)", cmd):
            expr, files = sm.group(1), sm.group(2).split()
            mm = re.match(r"s/((?:[^/\\]|\\.)*)/((?:[^/\\]|\\.)*)/(g?)$", expr)
            unesc = lambda x: re.sub(r"\\(.)", r"\1", x)  # noqa: E731
            for f in files:
                t = _target_of(f)
                if t:
                    events.append({"ts": ts, "id": tid, "kind": "sed", "target": t,
                                   "pairs": [(unesc(mm.group(1)), unesc(mm.group(2)), -1 if mm.group(3) else "perline")]})

def _apply(text_, a, b, n):
    if n == "perline":
        return "\n".join(l.replace(a, b, 1) for l in text_.split("\n"))
    return text_.replace(a, b) if n == -1 else text_.replace(a, b, 1)


def exec_forward(src, target, content):
    """Run the RECORDED script on `content` for real; return what it would have written."""
    written = {}

    def fake_open(p, mode="r", *a, **k):
        t = _target_of(p)
        if "w" in mode:
            buf = io.StringIO()
            buf.close_orig = buf.close
            def _close():
                written[t] = buf.getvalue()
            buf.close = _close
            return buf
        if t != target:
            raise FileNotFoundError(p)
        return io.StringIO(content)
    g = {"__name__": "__replay__", "open": fake_open, "__builtins__": builtins}
    real_sio_write = None
    exec(compile(src.replace("open(p,'w').write(s)", "_w=open(p,'w'); _w.write(s); _w.close()"), "<fwd>", "exec"), g)
    return written.get(target)


def reverse_slice(src, later):
    """Exact inverses of the two slice scripts used on routes/parts.py (2026-10-19 20:32 UTC)."""
    if "lines=block.split('\\n')" in src or "new=[lines[0]]+" in src:      # re-indent +4 after first line
        i = later.index('                fit_ids = (await db.execute(text(')
        j = later.index('                    " LIMIT :fit_id_cap")')
        lines = later[i:j].split("\n")
        back = [lines[0]] + [(l[4:] if l.startswith("    ") and l.strip() else l) for l in lines[1:]]
        return later[:i] + "\n".join(back) + later[j:], "reindent"
    if "if True:" in src:                                                   # drop `if True:` + dedent 4
        i = later.index('        original_task = _bucket(_PT_ORIGINAL)')
        end = '        return original_bucket, oem_bucket, aftermarket_bucket\n'
        j = later.index(end, i) + len(end)
        body = later[i:j].split("\n")
        back = ["        if True:"] + [("    " + l if l.strip() else l) for l in body]
        return later[:i] + "\n".join(back) + later[j:], "if_true"
    raise ValueError("unknown slice script")


events.sort(key=lambda e: e["ts"])
report = {"events": len(events), "files": {}, "unhandled": [e for e in events if e["kind"] in ("py_slice",)],
          "failed_scripts_skipped": [e["ts"] for e in events if e["kind"] == "py_failed"]}
for t in TARGETS:
    cur = (REPO / t).read_text(encoding="utf-8")
    evs = [e for e in events if e.get("target") == t]
    ok, problems, steps = True, [], 0
    for e in reversed(evs):
        before = cur
        if e["kind"] == "py_slice":
            # the slice scripts also contain plain replace() pairs that run BEFORE the slice
            pre_pairs = [(x[1], x[2], x[3]) for x in run_script(e["src"]) if x[0] == t]
            try:
                cur, how = reverse_slice(e["src"], cur)
            except Exception as ex:
                problems.append((e["ts"], f"slice reverse failed: {ex}"))
                ok = False
                break
            for a, b, n in reversed(pre_pairs):
                if cur.count(b) != 1:
                    (OUT / "debug_at_failure.txt").write_text(cur, encoding="utf-8")
                    problems.append((e["ts"], f"slice pre-pair new text occurs {cur.count(b)}x", b[:80]))
                    ok = False
                    break
                cur = cur.replace(b, a, 1)
            if not ok:
                break
            fwd = exec_forward(e["src"], t, cur)                  # the recorded script, for real
            if fwd != before:
                problems.append((e["ts"], f"slice forward exec != later content ({how})"))
                ok = False
                break
            steps += 1
            continue
        for a, b, n in reversed(e["pairs"]):
            c = cur.count(b) if b else None
            if not b:
                problems.append((e["ts"], "deletion: cannot place old text"))
                ok = False
                break
            if n == "perline":
                cur = "\n".join(l.replace(b, a, 1) for l in cur.split("\n"))
                continue
            if n == 1 and c != 1:
                problems.append((e["ts"], f"new text occurs {c}x (need 1)", b[:80]))
                ok = False
                break
            cur = cur.replace(b, a) if n == -1 else cur.replace(b, a, 1)
        if not ok:
            break
        fwd = cur
        for a, b, n in e["pairs"]:
            if a and fwd.count(a) < 1:
                problems.append((e["ts"], "forward: old text missing", a[:80]))
                ok = False
            fwd = _apply(fwd, a, b, n)
        if fwd != before:
            problems.append((e["ts"], "forward replay != later content"))
            ok = False
        if not ok:
            break
        steps += 1
    dest = OUT / t
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ok:
        dest.write_text(cur, encoding="utf-8")
    report["files"][t] = {"edits": len(evs), "undone": steps, "ok": ok, "problems": problems[:5]}
json.dump(report, open(OUT / "report.json", "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in report.items() if k != "unhandled"}, indent=1, default=str)[:4000])
print("unhandled slice scripts:", [(e["ts"], e["src"][:120]) for e in report["unhandled"]])
