"""
Script: runtime_identity.py
Purpose: Prove which source code a running process ACTUALLY loaded for a few watched modules (2026-10-28,
         release gate). A file hash on disk proves nothing about a long-running process: CPython executes the
         cached .pyc without re-reading the source, and a lazily imported module may load long after any check.
Process:
  install()   called from sitecustomize at every interpreter start. Wraps SourceFileLoader.get_code for the
              WATCHED modules only (env RUNTIME_IDENTITY_MODULES, default "agents.owner_console"): it reads the
              source file ONCE, hashes exactly those bytes, compiles exactly those bytes (the .pyc is bypassed for
              these modules only) and returns that code object - so the code that runs IS the hashed source.
              Every other module imports exactly as before.
  record      one JSON per load: module, path, sha256, size, pid, process start ticks (/proc/self/stat field 22),
              kernel boot id, container hostname, utc time - written atomically to
              $RUNTIME_IDENTITY_DIR (default /app/state/runtime_identity/<module>.<pid>.<starttime>.json) and logged
              as one "[runtime_identity]" line. No secrets, no customer data (paths and hashes only).
  loaded()    the in-process registry (also used by tests).
  Failure is never fatal: if anything here fails the import proceeds through the normal loader.
Data Imported/Modified: files under $RUNTIME_IDENTITY_DIR only.
Data Sources: the module source files, /proc/self/stat, /proc/sys/kernel/random/boot_id.
Last Updated: 2026-10-28
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import socket
import sys
from importlib.machinery import SourceFileLoader

DEFAULT_MODULES = "agents.owner_console"
_REGISTRY: dict = {}
_INSTALLED = False


def watched() -> frozenset:
    return frozenset(m.strip() for m in os.environ.get("RUNTIME_IDENTITY_MODULES", DEFAULT_MODULES).split(",")
                     if m.strip())


def process_start_ticks(pid: str = "self") -> str:
    try:
        data = open(f"/proc/{pid}/stat").read()
        return data.rsplit(")", 1)[1].split()[19]          # field 22 (starttime), robust to spaces in comm
    except Exception:
        return ""


def boot_id() -> str:
    try:
        return open("/proc/sys/kernel/random/boot_id").read().strip()
    except Exception:
        return ""


def state_dir() -> str:
    import app_paths                                       # lazy: this module is installed from sitecustomize in every interpreter
    return str(app_paths.state_path("RUNTIME_IDENTITY_DIR", "runtime_identity"))


def _write(rec: dict) -> None:
    try:
        d = state_dir()
        os.makedirs(d, exist_ok=True)
        name = f"{rec['module']}.{rec['pid']}.{rec['process_start_ticks'] or 'na'}.json"
        tmp = os.path.join(d, f".{name}.{os.getpid()}.tmp")
        with open(tmp, "w") as f:
            json.dump(rec, f, indent=1)
        os.replace(tmp, os.path.join(d, name))
    except Exception as exc:                               # read-only test mounts etc.: the log line remains - and says the record is missing
        try:
            sys.stderr.write(f"[runtime_identity] record NOT written to {state_dir()}: {exc}\n")
        except Exception:
            pass


def loaded() -> dict:
    return dict(_REGISTRY)


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    original = SourceFileLoader.get_code

    def get_code(self, fullname):
        if fullname not in watched():
            return original(self, fullname)
        try:
            path = self.get_filename(fullname)
            source = self.get_data(path)                    # the ONE read of the bytes we hash and compile
            code = self.source_to_code(source, path)
        except Exception:
            return original(self, fullname)
        rec = {"module": fullname, "path": os.path.abspath(path), "sha256": hashlib.sha256(source).hexdigest(),
               "size": len(source), "via": "source_compiled_by_runtime_identity", "pid": os.getpid(),
               "process_start_ticks": process_start_ticks(), "boot_id": boot_id(), "hostname": socket.gethostname(),
               "argv0": os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else "",
               "loaded_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
               "load_count": _REGISTRY.get(fullname, {}).get("load_count", 0) + 1}
        _REGISTRY[fullname] = rec
        _write(rec)
        try:
            sys.stderr.write(f"[runtime_identity] {fullname} sha256={rec['sha256']} pid={rec['pid']} "
                             f"start={rec['process_start_ticks']} path={rec['path']}\n")
        except Exception:
            pass
        return code

    SourceFileLoader.get_code = get_code
    _INSTALLED = True
    return True
