"""#118 — meili_purge_inactive must delete only ids that are still in the index (2026-11-03).

Pure unit tests against an in-memory fake Meilisearch (no network, no database). Defect guarded:
the purge re-sent every inactive id after each sync (50 documentDeletion tasks x 10,000 ids per run,
all with deletedDocuments=0), growing the Meilisearch task store to 11 GB.
"""
import importlib.util
import os
import pathlib
import sys

import pytest

_P = pathlib.Path(__file__).resolve().parent.parent / "maintenance" / "meili_purge_inactive.py"


@pytest.fixture()
def purge_mod(monkeypatch):
    if "asyncpg" not in sys.modules:
        try:
            import asyncpg  # noqa: F401
        except ImportError:
            monkeypatch.setitem(sys.modules, "asyncpg", type(sys)("asyncpg"))
    spec = importlib.util.spec_from_file_location("meili_purge_inactive_under_test", _P)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeMeili:
    """Holds a set of document ids; records every request; can inject failures."""

    def __init__(self, docs, fail_probe=0, fail_delete=0):
        self.docs = set(docs)
        self.fail_probe, self.fail_delete = fail_probe, fail_delete
        self.deletes = []   # one list per delete-batch task
        self.probes = 0

    def __call__(self, path, method="GET", body=None):
        if path.endswith("/documents/fetch"):
            self.probes += 1
            if self.fail_probe:
                self.fail_probe -= 1
                raise OSError("probe boom")
            wanted = set(__import__("json").loads("[" + body["filter"][len("id IN ["):-1] + "]"))
            return {"results": [{"id": i} for i in wanted if i in self.docs][: body["limit"]]}
        if path.endswith("/documents/delete-batch"):
            if self.fail_delete:
                self.fail_delete -= 1
                raise OSError("delete boom")
            self.deletes.append(list(body))
            self.docs -= set(body)
            return {"taskUid": len(self.deletes)}
        raise AssertionError(path)


def _ids(n, prefix="p"):
    return [f"{prefix}{i}" for i in range(n)]


def test_ids_already_absent_enqueue_nothing(purge_mod):
    m = FakeMeili(docs=_ids(10, "live"))
    tasks, errors = purge_mod.purge(_ids(500), m, sleep=lambda s: None)
    assert tasks == [] and errors == 0 and m.deletes == []


def test_only_ids_still_present_are_deleted(purge_mod):
    gone, stale = _ids(300, "gone"), _ids(40, "stale")
    m = FakeMeili(docs=stale + _ids(5, "live"))
    tasks, errors = purge_mod.purge(gone + stale, m, sleep=lambda s: None)
    assert errors == 0 and len(tasks) == 1
    assert sorted(m.deletes[0]) == sorted(stale)
    assert all(d.startswith("live") for d in m.docs)  # active docs untouched


def test_newly_deactivated_ids_are_deleted(purge_mod):
    m = FakeMeili(docs=["a", "b", "c"])
    tasks, errors = purge_mod.purge(["b"], m, sleep=lambda s: None)
    assert m.docs == {"a", "c"} and tasks == [1] and errors == 0


def test_empty_input(purge_mod):
    m = FakeMeili(docs=["a"])
    assert purge_mod.purge([], m, sleep=lambda s: None) == ([], 0)
    assert m.probes == 0 and m.deletes == []


def test_large_input_is_chunked_for_probe_and_delete(purge_mod, monkeypatch):
    monkeypatch.setattr(purge_mod, "PROBE_BATCH", 1000)
    monkeypatch.setattr(purge_mod, "BATCH", 2500)
    stale = _ids(6000, "s")
    m = FakeMeili(docs=stale)
    tasks, errors = purge_mod.purge(_ids(20000, "x") + stale, m, sleep=lambda s: None)
    assert errors == 0
    assert m.probes == 26                              # 26,000 ids / 1,000 per probe
    assert [len(d) for d in m.deletes] == [2500, 2500, 1000]
    assert m.docs == set()


def test_repeat_run_creates_no_duplicate_tasks(purge_mod):
    stale = _ids(50, "s")
    m = FakeMeili(docs=stale)
    first, _ = purge_mod.purge(stale, m, sleep=lambda s: None)
    second, _ = purge_mod.purge(stale, m, sleep=lambda s: None)
    assert len(first) == 1 and second == [] and len(m.deletes) == 1


def test_transient_probe_failure_is_retried(purge_mod):
    m = FakeMeili(docs=["a"], fail_probe=2)
    tasks, errors = purge_mod.purge(["a"], m, sleep=lambda s: None)
    assert errors == 0 and m.docs == set() and len(tasks) == 1


def test_persistent_probe_failure_never_falls_back_to_blind_delete(purge_mod):
    m = FakeMeili(docs=["a"], fail_probe=99)
    tasks, errors = purge_mod.purge(["a"], m, sleep=lambda s: None)
    assert tasks == [] and errors == 1 and m.deletes == [] and m.docs == {"a"}


def test_failed_delete_batch_does_not_stop_later_batches_and_next_run_finishes(purge_mod, monkeypatch):
    monkeypatch.setattr(purge_mod, "BATCH", 10)
    stale = _ids(25, "s")
    m = FakeMeili(docs=stale, fail_delete=1)
    tasks, errors = purge_mod.purge(stale, m, sleep=lambda s: None)
    assert errors == 1 and len(tasks) == 2 and len(m.docs) == 10   # first batch lost, others went
    tasks2, errors2 = purge_mod.purge(stale, m, sleep=lambda s: None)
    assert errors2 == 0 and m.docs == set()                        # idempotent recovery
